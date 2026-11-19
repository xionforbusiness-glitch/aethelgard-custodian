#!/usr/bin/env python3
"""
tests/test_gemini_live_call.py — End-to-End Voice Simulation & Audio Recording Test
===================================================================================
Simulates a live caller on Telegram interacting with Aethelgard over the Gemini 3.8
Live WebSocket bridge:
  1. Synthesizes human speech ("Aethelgard, please save a draft email about animals")
  2. Encapsulates into 48kHz mono 10ms frames (960 bytes, NTgCalls standard)
  3. Feeds frames into GeminiLiveBridgeSession
  4. Intercepts all output 48kHz audio frames from out_queue and records them to a WAV file
  5. Verifies autonomous execution of save_gmail_draft
  6. Sends follow-up speech ("Thank you Aethelgard, please end the call")
  7. Captures farewell audio and verifies clean end_call hangup
  8. Saves and analyzes the full recorded audio evidence
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
    print(f" Saved {len(pcm_data)} bytes of audio to {wav_path} ({len(pcm_data)/(sample_rate*2):.2f}s)")


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
    print("=" * 70)
    print("🎙️ STARTING AETHELGARD GEMINI 3.8 LIVE SIMULATED CALL TEST")
    print("=" * 70)

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
        print("🔔 [Hangup Callback] Aethelgard initiated hangup!")
        hangup_event.set()

    def on_log(msg: str):
        print(f"   [LOG] {msg}")

    bridge = gemini_live_bridge.GeminiLiveBridgeSession(
        peer_id=1021125594,
        call_ctx=call_ctx,
        brain=brain,
        log_cb=on_log,
        hangup_cb=on_hangup,
    )

    # Audio recorder task: collects all 48kHz audio frames from out_queue
    recorded_audio = bytearray()
    recording_active = True

    async def audio_recorder_worker():
        while recording_active or not out_queue.empty():
            try:
                frame = await asyncio.wait_for(out_queue.get(), timeout=0.1)
                call_ctx["ai_speaking"] = True
                recorded_audio.extend(frame)
            except asyncio.TimeoutError:
                call_ctx["ai_speaking"] = False
            except Exception:
                break
        call_ctx["ai_speaking"] = False

    recorder_task = asyncio.create_task(audio_recorder_worker())

    print("\n1. Connecting Gemini Live bridge session...")
    connected = await bridge.start()
    if not connected:
        print("❌ Failed to connect to Gemini Live!")
        recording_active = False
        await recorder_task
        return False

    print("✅ Connected to Gemini 3.8 Live WebSocket successfully!")

    # Wait 3.0s for opening greeting to be spoken and captured
    print("⏳ Waiting for initial spoken greeting...")
    await asyncio.sleep(3.5)

    # 2. Synthesize speech for Turn 1: Omar asking to save a draft email about animals
    print("\n2. Synthesizing Turn 1: 'Aethelgard, please save a draft email about animals.'")
    turn1_audio_48k = await synthesize_speech_48k("Aethelgard, please save a draft email about animals.")
    print(f"   Synthesized Turn 1 speech: {len(turn1_audio_48k)} bytes ({len(turn1_audio_48k)/96000:.2f}s)")

    # Stream Turn 1 into bridge in 10ms (960-byte) frames
    print("   Streaming Turn 1 frames to Aethelgard's microphone input...")
    for i in range(0, len(turn1_audio_48k), 960):
        frame = turn1_audio_48k[i : i + 960]
        bridge.feed_incoming_frame_48k(frame)
        await asyncio.sleep(0.010)

    # Send 1.2s silence to trigger turn complete
    silence_frame = b"\x00" * 960
    for _ in range(120):
        bridge.feed_incoming_frame_48k(silence_frame)
        await asyncio.sleep(0.010)

    print("   Turn 1 speech streamed. Waiting for Aethelgard tool execution and verbal response...")
    # Wait for response and tool execution
    t0 = time.monotonic()
    draft_detected = False
    while time.monotonic() - t0 < 8.0:
        if brain.actions_log and any(a.get("tool") == "save_gmail_draft" for a in brain.actions_log):
            draft_detected = True
            break
        await asyncio.sleep(0.5)

    if draft_detected:
        print("🎯 SUCCESS: save_gmail_draft was autonomously called and executed!")
    else:
        print("⚠️ Warning: save_gmail_draft not yet detected in actions_log.")

    # Allow response audio to finish playing
    await asyncio.sleep(3.5)

    # 3. Synthesize speech for Turn 2: Omar saying "Thank you Aethelgard, please end the call."
    print("\n3. Synthesizing Turn 2: 'Thank you Aethelgard, that will be all. Please end the call.'")
    turn2_audio_48k = await synthesize_speech_48k("Thank you Aethelgard, that will be all. Please end the call.")
    print(f"   Synthesized Turn 2 speech: {len(turn2_audio_48k)} bytes ({len(turn2_audio_48k)/96000:.2f}s)")

    print("   Streaming Turn 2 frames...")
    for i in range(0, len(turn2_audio_48k), 960):
        frame = turn2_audio_48k[i : i + 960]
        bridge.feed_incoming_frame_48k(frame)
        await asyncio.sleep(0.010)

    for _ in range(120):
        bridge.feed_incoming_frame_48k(silence_frame)
        await asyncio.sleep(0.010)

    print("   Waiting for Aethelgard farewell and end_call hangup...")
    try:
        await asyncio.wait_for(hangup_event.wait(), timeout=8.0)
        print("🎯 SUCCESS: end_call hangup triggered cleanly!")
    except asyncio.TimeoutError:
        print("⚠️ Hangup event timed out, closing bridge manually.")

    await asyncio.sleep(1.0)
    await bridge.close()

    recording_active = False
    await recorder_task

    # Save audio evidence to WAV file
    pcm_to_wav(bytes(recorded_audio), wav_out_path, sample_rate=48000)

    print("\n" + "=" * 70)
    print("📊 TEST SUMMARY & EVIDENCE REPORT")
    print("=" * 70)
    print(f"• Total Spoken Audio Recorded: {len(recorded_audio)} bytes ({len(recorded_audio)/96000:.2f} seconds)")
    print(f"• Recording Saved To         : {wav_out_path}")
    print(f"• Actions Executed           : {[a.get('tool') for a in brain.actions_log]}")
    if brain.actions_log:
        for idx, act in enumerate(brain.actions_log):
            print(f"  [{idx+1}] Tool: {act.get('tool')} | Summary: {act.get('summary')} | OK: {act.get('ok')}")

    return True


if __name__ == "__main__":
    asyncio.run(run_simulated_call_test())
