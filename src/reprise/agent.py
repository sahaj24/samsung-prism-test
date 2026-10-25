import asyncio
import json
import os
import time
from dataclasses import asdict, replace
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
from .call_activation import (ACTIVATION_VERSION, CallActivation, PROMPT as ACTIVATION_PROMPT,
                              activation_enabled)
from .catalog import TOOLS, ToolSpec
from .config import ROOT, Settings, credential, source_digest
from .coordinator import ClarificationRequired, Coordinator, InvalidCall, OutcomeUnknown, Superseded
from .prompts import BENCHMARK_PROMPT, PRODUCTIVITY_PROMPT
from .session_events import attach_session_events
from .speech import watch_microphone
from .trace import Trace


def make_tool(spec: ToolSpec, coordinator: Coordinator):
    async def invoke(raw_arguments: dict, context: RunContext):
        try:
            gate = coordinator.activation
            if gate and not gate.allows_response(context.speech_handle.id):
                coordinator.trace.event("activation_tool_blocked", function=spec.name,
                                        response_id=context.speech_handle.id, state=gate.state)
                raise StopResponse()
            return await coordinator.execute(
                spec, raw_arguments,
                interrupted=lambda: context.speech_handle.interrupted,
                provider_call_id=context.function_call.call_id,
                response_id=context.speech_handle.id,
            )
        except Superseded:
            raise StopResponse() from None
        except ClarificationRequired as exc:
            # Stop the non-blocking Live loop, not any already-dispatched write.
            # Concurrent failed proposals can schedule only one clarification.
            if exc.input_id == coordinator.current_input_id and exc.input_id not in coordinator._recovery_notices:
                coordinator._recovery_notices.add(exc.input_id)
                context.session.generate_reply(
                    instructions="Say this clarification, then wait for the user. Do not claim success or promise background work: " + str(exc),
                    tool_choice="none", tools=[],
                )
            raise StopResponse() from None
        except (InvalidCall, OutcomeUnknown) as exc:
            coordinator.trace.event("tool_rejected", function=spec.name, reason=str(exc))
            raise ToolError(str(exc)) from None
    return llm.function_tool(invoke, raw_schema=spec.declaration())


def make_text_input_handler(coordinator):
    default_handler = room_io.TextInputOptions().text_input_cb
    async def handle(session, event):
        # Observe before interruption/generation, using the same safety boundary as audio.
        coordinator.observe_transcript(event.text, is_final=True, source="text")
        if coordinator.activation and not coordinator.activation.allows_tools:
            # Keep typed background conversation in the same model context, without
            # default_handler's generate_reply. Audio already enters Gemini directly.
            agent = session.current_agent
            history = agent.chat_ctx.copy()
            history.add_message(role="user", content=event.text)
            await agent.update_chat_ctx(history)
            coordinator.trace.event("conversation_item", role="user", text=event.text)
            return
        await default_handler(session, event)
    return handle


class CallListeningAgent(Agent):
    def __init__(self, *, instructions, activation):
        super().__init__(instructions=instructions)
        self.activation = activation

    def realtime_audio_output_node(self, audio, model_settings):
        speech = self.session.current_speech
        return self.activation.filter_output(audio, speech.id if speech else None)

    def transcription_node(self, text, model_settings):
        speech = self.session.current_speech
        return self.activation.filter_output(text, speech.id if speech else None)


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
    if not isinstance(room_metadata, dict):
        room_metadata = {}
    if room_name.startswith("reprise-demo-") and room_metadata.get("reprise_mode") in {"extension", "productivity", "benchmark", "call_assist"}:
        settings = replace(settings, mode=room_metadata["reprise_mode"])
    directory = ROOT / "runs" / room_name
    trace = Trace(room_name, directory, Path("/tmp/agent_tool_calls.log"))
    if settings.mode == "call_assist":
        from .call_assist import PROMPT, TOOLS as ASSIST_TOOLS, make_backend
        provider = room_metadata.get("workspace_backend", "mock")
        if provider not in {"mock", "google"}:
            raise RuntimeError("Unsupported Call Assist workspace transport")
        # A forged room flag cannot enable Google writes without server configuration.
        if provider == "google":
            from .call_assist import workspace_backend
            if workspace_backend() != "google":
                raise RuntimeError("Call Assist Google writes are disabled")
        backend, specs, prompt = make_backend(room_name, directory, trace, provider), ASSIST_TOOLS, PRODUCTIVITY_PROMPT + PROMPT
        trace.official_path = None
        settings = replace(settings, tool_timeout_s=180)
    elif settings.mode == "productivity":
        from .productivity import PRODUCTIVITY_TOOLS, ProductivityBackend
        backend, specs, prompt = ProductivityBackend(room_name, trace), PRODUCTIVITY_TOOLS, PRODUCTIVITY_PROMPT
        trace.official_path = None
        settings = replace(settings, tool_timeout_s=180)
    else:
        backend, specs, prompt = FDBBackend(settings.latency_profile), TOOLS, BENCHMARK_PROMPT
    coordinator = Coordinator(backend, trace, settings)
    trace.event("session_started", model=settings.model, policy=settings.policy,
                settle_ms=settings.settle_ms, write_settle_ms=settings.write_settle_ms,
                mode=settings.mode, settings=asdict(settings), source_sha256=source_digest(),
                presentation=room_metadata.get("presentation"),
                activation_version=room_metadata.get("activation_version"),
                vad_silence_ms=int(os.getenv("REPRISE_VAD_SILENCE_MS", "650")),
                audio_clock_origin="not synchronized to client stream")
    session = AgentSession(llm=build_model(settings),
                           tools=[make_tool(s, coordinator) for s in specs],
                           max_tool_steps=12 if settings.mode in {"productivity", "call_assist"} else 8)
    monitor_task = None
    activation = CallActivation(coordinator, enabled=activation_enabled(room_name, room_metadata))
    coordinator.activation = activation
    agent = CallListeningAgent(instructions=prompt + (ACTIVATION_PROMPT if activation.enabled else ""),
                               activation=activation)

    def activation_changed():
        if activation.enabled and activation.state == "standby" and session.current_speech:
            session.interrupt(force=True)
    activation.on_change = activation_changed

    @ctx.room.local_participant.register_rpc_method("reprise.presentation")
    async def set_presentation(data):
        participant = ctx.room.remote_participants.get(data.caller_identity)
        if not participant or not participant.identity.startswith("demo-"):
            raise ValueError("Only the connected demo participant can select a presentation")
        request = json.loads(data.payload)
        mode = request.get("mode")
        if request.get("activation_version") != ACTIVATION_VERSION:
            raise ValueError("Reload the demo: activation protocol mismatch")
        if mode not in {"live", "demo-live"}:
            raise ValueError("Invalid presentation")
        activation.set_enabled(mode == "demo-live")
        await agent.update_instructions(prompt + (ACTIVATION_PROMPT if activation.enabled else ""))
        return json.dumps({"enabled": activation.enabled, "state": activation.state,
                           "presentation": mode, "activation_version": ACTIVATION_VERSION, "epoch": activation.epoch})

    attach_session_events(session, coordinator)

    async def shutdown():
        if monitor_task:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        await coordinator.close()
        if settings.mode in {"productivity", "call_assist"}:
            await backend.close()
        trace.write_snapshot(coordinator.snapshot())
        trace.event("session_closed")

    ctx.add_shutdown_callback(shutdown)
    await session.start(
        room=ctx.room, agent=agent,
        room_options=room_io.RoomOptions(
            video_input=False,
            text_input=room_io.TextInputOptions(text_input_cb=make_text_input_handler(coordinator)),
        ),
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
