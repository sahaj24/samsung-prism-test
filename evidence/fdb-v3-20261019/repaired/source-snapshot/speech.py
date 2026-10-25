"""Acoustic speech boundary monitor for Gemini's model-initiated generation quirk."""
import asyncio

from livekit import rtc
from livekit.agents.vad import VADEventType
from livekit.plugins import silero


async def watch_microphone(participant, coordinator):
    """Feed the same remote microphone to an independent VAD without consuming it."""
    vad = await asyncio.to_thread(
        silero.VAD.load, min_speech_duration=0.08,
        min_silence_duration=0.45, prefix_padding_duration=0.2,
        sample_rate=16000,
    )
    audio = rtc.AudioStream.from_participant(
        participant=participant, track_source=rtc.TrackSource.SOURCE_MICROPHONE,
        sample_rate=16000, num_channels=1, frame_size_ms=20,
    )
    stream = vad.stream()

    async def feed():
        async for packet in audio:
            stream.push_frame(packet.frame)
        stream.end_input()

    feeding = asyncio.create_task(feed())
    try:
        async for event in stream:
            if event.type == VADEventType.START_OF_SPEECH:
                coordinator.speech_started()
            elif event.type == VADEventType.END_OF_SPEECH:
                coordinator.speech_ended()
    finally:
        feeding.cancel()
        await asyncio.gather(feeding, return_exceptions=True)
        await audio.aclose()
        await stream.aclose()
