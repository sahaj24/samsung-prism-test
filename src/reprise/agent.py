import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path

from google.genai import types
from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
    StopResponse,
    ToolError,
    llm,
    room_io,
)
from livekit.plugins import google

from .backend import FDBBackend
from .catalog import TOOLS, ToolSpec
from .config import ROOT, Settings, credential
from .coordinator import Coordinator, InvalidCall, OutcomeUnknown, Superseded
from .prompts import BENCHMARK_PROMPT, EXTENSION_PROMPT
from .speech import watch_microphone
from .trace import Trace


def make_tool(spec: ToolSpec, coordinator: Coordinator):
    async def invoke(raw_arguments: dict, context: RunContext):
        try:
            return await coordinator.execute(
                spec, raw_arguments,
                interrupted=lambda: context.speech_handle.interrupted,
            )
        except Superseded as exc:
            coordinator.trace.event("tool_superseded", function=spec.name, reason=str(exc))
            raise StopResponse() from None
        except (InvalidCall, OutcomeUnknown) as exc:
            coordinator.trace.event("tool_rejected", function=spec.name, reason=str(exc))
            raise ToolError(str(exc)) from None
    return llm.function_tool(invoke, raw_schema=spec.declaration())


def build_model(settings: Settings):
    key, mode = credential()
    if not key:
        raise RuntimeError("Set GOOGLE_API_KEY in .env.local.")
    return google.realtime.RealtimeModel(
        model=settings.model,
        api_key=key,
        api_version="v1beta" if mode == "ephemeral" else "v1alpha",
        modalities=[types.Modality.AUDIO],
        voice="Puck",
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        tool_behavior=types.Behavior.NON_BLOCKING,
        tool_response_scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                silence_duration_ms=int(os.getenv("REPRISE_VAD_SILENCE_MS", "650")),
                prefix_padding_ms=300,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            ),
        ),
        # Gemini 3.8 Live rejects thinking_level and affective dialogue settings.
    )


server = AgentServer(port=0, num_idle_processes=1, initialize_process_timeout=40,
                     drain_timeout=15, load_threshold=0.95)


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    settings = Settings.from_env()
    await ctx.connect()
    room_name = ctx.room.name
    try:
        room_metadata = json.loads(ctx.room.metadata or "{}")
    except json.JSONDecodeError:
        room_metadata = {}
    if room_name.startswith("reprise-demo-") and room_metadata.get("reprise_mode") == "extension":
        settings = replace(settings, mode="extension")
    directory = ROOT / "runs" / room_name
    trace = Trace(room_name, directory, Path("/tmp/agent_tool_calls.log"))
    if settings.mode == "extension":
        from .extension import MANUAL_TOOLS, ManualBackend
        backend, specs, prompt = ManualBackend(trace), MANUAL_TOOLS, EXTENSION_PROMPT
        trace.official_path = None
    else:
        backend, specs, prompt = FDBBackend(settings.latency_profile), TOOLS, BENCHMARK_PROMPT
    coordinator = Coordinator(backend, trace, settings)
    trace.event("session_started", model=settings.model, policy=settings.policy,
                settle_ms=settings.settle_ms, write_settle_ms=settings.write_settle_ms,
                mode=settings.mode)
    session = AgentSession(llm=build_model(settings),
                           tools=[make_tool(s, coordinator) for s in specs],
                           max_tool_steps=8)
    monitor_task = None

    @session.on("user_input_transcribed")
    def transcript(event):
        coordinator.observe_transcript(event.transcript, is_final=event.is_final)

    @session.on("conversation_item_added")
    def conversation(event):
        item = event.item
        if hasattr(item, "role"):
            if item.role == "user":
                coordinator.remember_user_message(item.text_content)
            trace.event("conversation_item", role=item.role, text=item.text_content,
                        interrupted=getattr(item, "interrupted", False))

    @session.on("agent_state_changed")
    def agent_state(event):
        trace.event("agent_state", state=event.new_state)
        if event.new_state == "listening" and event.old_state == "speaking":
            coordinator.finish_response()

    @session.on("error")
    def error(event):
        # Never persist arbitrary exception strings: provider errors can contain URLs/keys.
        trace.event("session_error", error_type=type(event.error).__name__)

    async def shutdown():
        if monitor_task:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        await coordinator.close()
        (directory / "snapshot.json").write_text(json.dumps(coordinator.snapshot(), indent=2))
        trace.event("session_closed")

    ctx.add_shutdown_callback(shutdown)
    await session.start(
        room=ctx.room, agent=Agent(instructions=prompt),
        room_options=room_io.RoomOptions(video_input=False),
    )
    async def monitor_speech():
        try:
            participant = await ctx.wait_for_participant()
            await watch_microphone(participant, coordinator)
        except Exception as exc:
            trace.event("speech_monitor_error", error_type=type(exc).__name__)

    monitor_task = asyncio.create_task(monitor_speech())
    trace.event("agent_ready")
    # Official harness readiness signal, with no credentials.
    with open("/tmp/agent_heartbeat.log", "a") as f:
        f.write(f"REPRISE READY room={room_name} time={time.time()}\n")


def main():
    Settings.from_env()
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
