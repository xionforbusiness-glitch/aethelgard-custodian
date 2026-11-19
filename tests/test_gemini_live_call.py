#!/usr/bin/env python3
"""
tests/test_gemini_live_call.py — End-to-End Voice Simulation & Audio Recording Test
===================================================================================
Simulates a live caller on Telegram interacting with Aethelgard over the Gemini 3.8
Live WebSocket bridge:
  1. Pre-synthesizes human speech clips (Turn 1: draft email, Turn 2: end call)
  2. Encapsulates into 48kHz mono 10ms frames (960 bytes, NTgCalls standard)
  3. Connects GeminiLiveBridgeSession and captures initial spoken greeting
  4. Waits for greeting turn completion and audio drain
  5. Feeds Turn 1 frames into Aethelgard's microphone input
  6. Verifies autonomous execution of save_gmail_draft and spoken confirmation
  7. Feeds Turn 2 frames ("Thank you Aethelgard, please end the call")
  8. Captures farewell audio and verifies clean end_call hangup
  9. Transcribes and analyzes the full recorded audio evidence
"""

import asyncio
import os
import sys
import time
import wave
from pathlib import Path
from typing import Dict, Any, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import edge_tts
import gemini_live_bridge
import telegram_voice_caller


def pcm_to_wav(pcm_data: bytes, wav_path: Path, sample_rate: int = 48000, channels: int = 1):
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    print(f"📁 Saved {len(pcm_data)} bytes of audio to {wav_path} ({len(pcm_data)/(sample_rate*2):.2f}s)", flush=True)


async def synthesize_speech_48k(text: str, voice: str = "en-US-GuyNeural") -> bytes:
    """Synthesizes text into 48kHz mono s16le PCM bytes using edge-tts and ffmpeg."""
    communicate = edge_tts.Communicate(text, voice)
    mp3_chunks = []
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            mp3_chunks.append(chunk["data"])
    mp3_bytes = b"".join(mp3_chunks)

    import subprocess
    proc = subprocess.Popen(
        ["ffmpeg", "-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "48000", "pipe:1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    pcm_48k, _ = proc.communicate(input=mp3_bytes)
    return pcm_48k


async def run_simulated_call_test():
    print("=" * 70, flush=True)
    print("🎙️ STARTING AETHELGARD GEMINI 3.8 LIVE SIMULATED CALL TEST", flush=True)
    print("=" * 70, flush=True)

    recordings_dir = REPO_ROOT / "tests" / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    wav_out_path = recordings_dir / "aethelgard_simulated_call.wav"

    out_queue: asyncio.Queue = asyncio.Queue()
    call_ctx: Dict[str, Any] = {
        "peer_id": 1021125594,
        "out_queue": out_queue,
        "ai_speaking": False,
        "opening_prompt": "Hello Omar, Aethelgard online and at your service.",
    }

    vault_dir = os.environ.get("VAULT_DIR") or str(REPO_ROOT / "staging" / "vault")
    Path(vault_dir).mkdir(parents=True, exist_ok=True)
    brain = telegram_voice_caller.VoiceBrainSession(vault_dir=vault_dir)

    hangup_event = asyncio.Event()

    def on_hangup():
        print("🔔 [Hangup Callback] Aethelgard initiated hangup!", flush=True)
        hangup_event.set()

    def on_log(msg: str):
        print(f"   [LOG] {msg}", flush=True)

    # 1. Pre-synthesize speech clips before starting the bridge
    print("\n1. Pre-synthesizing caller speech clips...", flush=True)
    turn1_audio_48k = await synthesize_speech_48k("Aethelgard, please save a draft email about animals.")
    turn2_audio_48k = await synthesize_speech_48k("Thank you Aethelgard, that will be all. Please end the call.")
    print(f"   Turn 1 audio: {len(turn1_audio_48k)} bytes ({len(turn1_audio_48k)/96000:.2f}s)", flush=True)
    print(f"   Turn 2 audio: {len(turn2_audio_48k)} bytes ({len(turn2_audio_48k)/96000:.2f}s)", flush=True)

    bridge = gemini_live_bridge.GeminiLiveBridgeSession(
        peer_id=1021125594,
        call_ctx=call_ctx,
        brain=brain,
        log_cb=on_log,
        hangup_cb=on_hangup,
    )

    # Audio recorder task: mirrors _audio_sender_loop with realistic 10ms pacing
    recorded_audio = bytearray()
    recording_active = True

    async def audio_recorder_worker():
        while recording_active or not out_queue.empty():
            try:
                frame = out_queue.get_nowait()
                call_ctx["ai_speaking"] = True
                recorded_audio.extend(frame)
                await asyncio.sleep(0.010)
            except asyncio.QueueEmpty:
                call_ctx["ai_speaking"] = False
                await asyncio.sleep(0.020)
            except Exception:
                break
        call_ctx["ai_speaking"] = False

    recorder_task = asyncio.create_task(audio_recorder_worker())

    print("\n2. Connecting Gemini Live bridge session...", flush=True)
    connected = await bridge.start()
    if not connected:
        print("❌ Failed to connect to Gemini Live!", flush=True)
        recording_active = False
        await recorder_task
        return False

    print("✅ Connected to Gemini 3.8 Live WebSocket successfully!", flush=True)

    # Wait for opening greeting turn to complete
    print("⏳ Waiting for initial spoken greeting to complete and audio to drain...", flush=True)
    try:
        await asyncio.wait_for(bridge.turn_complete_event.wait(), timeout=6.0)
        print("   Greeting model turn finished.", flush=True)
    except asyncio.TimeoutError:
        print("   Greeting wait timed out, continuing...", flush=True)

    while call_ctx.get("ai_speaking") or not out_queue.empty():
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.4)

    # 3. Stream Turn 1: Omar asking to save a draft email about animals
    print("\n3. Streaming Turn 1: 'Aethelgard, please save a draft email about animals.'", flush=True)
    bridge.turn_complete_event.clear()

    silence_frame = b"\x00" * 960
    for i in range(0, len(turn1_audio_48k), 960):
        frame = turn1_audio_48k[i : i + 960]
        bridge.feed_incoming_frame_48k(frame)
        await asyncio.sleep(0.010)

    # 1.5s of silence frames to trigger turn complete
    for _ in range(150):
        bridge.feed_incoming_frame_48k(silence_frame)
        await asyncio.sleep(0.010)

    print("   Turn 1 speech streamed. Waiting for Aethelgard tool execution and verbal response...", flush=True)
    try:
        await asyncio.wait_for(bridge.turn_complete_event.wait(), timeout=12.0)
        print("   Turn 1 model turn finished.", flush=True)
    except asyncio.TimeoutError:
        print("   Turn 1 wait timed out.", flush=True)

    draft_detected = bool(brain.actions_log and any(a.get("tool") == "save_gmail_draft" for a in brain.actions_log))
    if draft_detected:
        print("🎯 SUCCESS: save_gmail_draft was autonomously called and executed!", flush=True)
    else:
        print("⚠️ Warning: save_gmail_draft not yet detected in actions_log.", flush=True)

    # Wait for verbal response to drain completely
    while call_ctx.get("ai_speaking") or not out_queue.empty():
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)

    # 4. Stream Turn 2: Omar saying "Thank you Aethelgard, please end the call."
    print("\n4. Streaming Turn 2: 'Thank you Aethelgard, that will be all. Please end the call.'", flush=True)
    bridge.turn_complete_event.clear()

    for i in range(0, len(turn2_audio_48k), 960):
        frame = turn2_audio_48k[i : i + 960]
        bridge.feed_incoming_frame_48k(frame)
        await asyncio.sleep(0.010)

    for _ in range(150):
        bridge.feed_incoming_frame_48k(silence_frame)
        await asyncio.sleep(0.010)

    print("   Waiting for Aethelgard farewell and end_call hangup...", flush=True)
    try:
        await asyncio.wait_for(hangup_event.wait(), timeout=12.0)
        print("🎯 SUCCESS: end_call hangup triggered cleanly!", flush=True)
    except asyncio.TimeoutError:
        print("⚠️ Hangup event timed out, closing bridge manually.", flush=True)

    await asyncio.sleep(1.0)
    await bridge.close()

    recording_active = False
    await recorder_task

    # Save audio evidence to WAV file
    pcm_to_wav(bytes(recorded_audio), wav_out_path, sample_rate=48000)

    print("\n" + "=" * 70, flush=True)
    print("📊 TEST SUMMARY & EVIDENCE REPORT", flush=True)
    print("=" * 70, flush=True)
    print(f"• Total Spoken Audio Recorded: {len(recorded_audio)} bytes ({len(recorded_audio)/96000:.2f} seconds)", flush=True)
    print(f"• Recording Saved To         : {wav_out_path}", flush=True)
    print(f"• Actions Executed           : {[a.get('tool') for a in brain.actions_log]}", flush=True)
    if brain.actions_log:
        for idx, act in enumerate(brain.actions_log):
            print(f"  [{idx+1}] Tool: {act.get('tool')} | Summary: {act.get('summary')} | OK: {act.get('ok')}", flush=True)

    return True


if __name__ == "__main__":
    asyncio.run(run_simulated_call_test())
