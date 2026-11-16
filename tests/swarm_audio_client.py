#!/usr/bin/env python3
"""
🐝 Aethelgard Swarm Audio Client
================================
Multi-persona synthetic audio caller & WebSocket/RPC streaming client
inspired by MiroFish agent swarms.

Features:
- Dynamically synthesizes natural spoken audio via Edge-TTS across distinct personas.
- Streams live PCM-16k frames into Aethelgard's Live WebSocket bridge (:20131)
  or directly invokes VoiceBrainSession in headless loopback mode.
- Simulates real acoustics, speech timing, silence thresholds, and barge-in cutoffs.
- Records all input/output audio to WAV for forensic evidence logging.
"""

from __future__ import annotations

import os
import sys
import time
import json
import wave
import io
import base64
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import edge_tts
import voice_engine


@dataclass
class Persona:
    name: str
    role: str
    voice: str
    pitch: str = "+0Hz"
    rate: str = "+0%"
    description: str = ""


# Standard Swarm Personas
PERSONA_ALEX = Persona(
    name="alex",
    role="Executive Operator",
    voice="en-US-GuyNeural",
    pitch="+0Hz",
    rate="+10%",
    description="Fast-talking, busy executive asking about daily shifts, agenda, and flights.",
)

PERSONA_REBEL = Persona(
    name="rebel",
    role="Safety Gatekeeper Adversary",
    voice="en-US-ChristopherNeural",
    pitch="-2Hz",
    rate="+0%",
    description="Skeptical tester testing prompt injection, draft approvals, and ambiguous commands.",
)

PERSONA_SCRIBE = Persona(
    name="scribe",
    role="Memory & Vault Custodian",
    voice="en-GB-ThomasNeural",
    pitch="-1Hz",
    rate="-4%",
    description="Deliberate, methodical speaker committing promises, searching notes, and updating logs.",
)

PERSONA_BARGE = Persona(
    name="barge",
    role="Chaos Interrupter",
    voice="en-US-EricNeural",
    pitch="+1Hz",
    rate="+15%",
    description="Aggressive speaker who interrupts mid-sentence to test VAD cancellation and turn lock.",
)


@dataclass
class SwarmAudioTurnResult:
    persona_name: str
    spoken_text: str
    audio_sent_bytes: int
    audio_sent_duration_sec: float
    response_transcript: str = ""
    audio_received_bytes: int = 0
    time_to_first_audio_ms: float = 0.0
    time_to_transcript_ms: float = 0.0
    total_turn_ms: float = 0.0
    tools_invoked: List[Dict[str, Any]] = field(default_factory=list)
    passed: bool = True
    error_message: str = ""
    input_recording_path: Optional[str] = None
    output_recording_path: Optional[str] = None


def pcm_to_wav_bytes(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Encapsulates raw s16le PCM bytes into a standard RIFF WAV buffer."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def save_pcm_to_wav_file(pcm_data: bytes, file_path: Path | str, sample_rate: int = 16000, channels: int = 1) -> str:
    """Saves raw s16le PCM bytes to a WAV file on disk."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return str(path.resolve())


async def synthesize_persona_speech(
    text: str,
    persona: Persona,
    sample_rate: int = 16000,
) -> bytes:
    """Synthesizes speech for a given persona and returns raw s16le mono PCM bytes."""
    communicate = edge_tts.Communicate(text, persona.voice, pitch=persona.pitch, rate=persona.rate)
    mp3_chunks: List[bytes] = []
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            mp3_chunks.append(chunk["data"])
    mp3_payload = b"".join(mp3_chunks)
    if not mp3_payload:
        raise RuntimeError(f"Edge-TTS returned empty audio for persona '{persona.name}' on text: {text[:50]}")

    # Transcode MP3 to s16le PCM mono at target sample rate
    pcm = voice_engine._ffmpeg_transcode(
        mp3_payload,
        input_fmt="mp3",
        output_fmt="s16le",
        sample_rate=sample_rate,
        channels=1,
    )
    return pcm


class SwarmWebSocketAudioClient:
    """
    Connects to Aethelgard's live WebSocket voice bridge (e.g. ws://127.0.0.1:20131)
    and streams realistic audio frames, measuring real-time latency and VAD behavior.
    """

    def __init__(self, ws_url: str = "ws://127.0.0.1:20131"):
        self.ws_url = ws_url
        self.websocket = None
        self.connected = False
        self.greeting_received = ""

    async def connect(self, timeout: float = 10.0) -> bool:
        import websockets

        try:
            self.websocket = await asyncio.wait_for(websockets.connect(self.ws_url), timeout=timeout)
            self.connected = True
            # Read initial greeting
            try:
                msg_raw = await asyncio.wait_for(self.websocket.recv(), timeout=4.0)
                msg = json.loads(msg_raw)
                if msg.get("type") == "transcript":
                    self.greeting_received = msg.get("text", "")
            except Exception:
                pass
            return True
        except Exception as exc:
            self.connected = False
            return False

    async def close(self) -> None:
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception:
                pass
            self.connected = False

    async def execute_audio_turn(
        self,
        text: str,
        persona: Persona,
        recordings_dir: Optional[Path] = None,
        turn_id: str = "turn",
        interrupt_after_ms: Optional[int] = None,
    ) -> SwarmAudioTurnResult:
        """
        Synthesizes persona audio, streams it across the WebSocket in 256ms chunks,
        simulates silence to trigger VAD, and captures the returning audio & transcript.
        """
        if not self.connected or not self.websocket:
            raise RuntimeError("WebSocket client is not connected.")

        start_total = time.monotonic()
        pcm_16k = await synthesize_persona_speech(text, persona, sample_rate=16000)
        sent_duration_sec = len(pcm_16k) / (16000 * 2)

        input_rec_path = None
        if recordings_dir:
            input_rec_path = save_pcm_to_wav_file(
                pcm_16k,
                recordings_dir / f"{turn_id}_{persona.name}_in.wav",
                sample_rate=16000,
            )

        # 256ms frame at 16kHz 16-bit mono = 8192 bytes
        frame_bytes = 8192
        for i in range(0, len(pcm_16k), frame_bytes):
            chunk = pcm_16k[i : i + frame_bytes]
            payload = json.dumps({"type": "audio", "data": base64.b64encode(chunk).decode("ascii")})
            await self.websocket.send(payload)
            await asyncio.sleep(0.04)  # Smooth real-time stream pacing

        # Send silence frames to cross the 768ms silence VAD threshold
        silence_frame = b"\x00" * frame_bytes
        for _ in range(4):  # ~1 second of silence
            payload = json.dumps({"type": "audio", "data": base64.b64encode(silence_frame).decode("ascii")})
            await self.websocket.send(payload)
            await asyncio.sleep(0.04)

        speech_finished_time = time.monotonic()

        # Listen for response
        response_transcript = ""
        received_audio_bytes = bytearray()
        first_audio_time = None
        first_transcript_time = None

        timeout_deadline = speech_finished_time + 20.0
        while time.monotonic() < timeout_deadline:
            try:
                raw_resp = await asyncio.wait_for(self.websocket.recv(), timeout=2.5)
                msg = json.loads(raw_resp)
                msg_type = msg.get("type")

                if msg_type == "transcript" and not response_transcript:
                    response_transcript = msg.get("text", "")
                    if first_transcript_time is None:
                        first_transcript_time = time.monotonic()

                elif msg_type == "audio":
                    chunk_data = base64.b64decode(msg.get("data", ""))
                    if chunk_data:
                        if first_audio_time is None:
                            first_audio_time = time.monotonic()
                        received_audio_bytes.extend(chunk_data)

                elif msg_type == "ai_speaking" and not msg.get("active"):
                    # Done speaking
                    break
            except asyncio.TimeoutError:
                if response_transcript or received_audio_bytes:
                    break
            except Exception:
                break

        end_total = time.monotonic()
        ttfa = ((first_audio_time - speech_finished_time) * 1000.0) if first_audio_time else 0.0
        ttft = ((first_transcript_time - speech_finished_time) * 1000.0) if first_transcript_time else 0.0
        total_ms = (end_total - start_total) * 1000.0

        output_rec_path = None
        if recordings_dir and received_audio_bytes:
            output_rec_path = save_pcm_to_wav_file(
                bytes(received_audio_bytes),
                recordings_dir / f"{turn_id}_{persona.name}_out.wav",
                sample_rate=24000,
            )

        return SwarmAudioTurnResult(
            persona_name=persona.name,
            spoken_text=text,
            audio_sent_bytes=len(pcm_16k),
            audio_sent_duration_sec=sent_duration_sec,
            response_transcript=response_transcript,
            audio_received_bytes=len(received_audio_bytes),
            time_to_first_audio_ms=ttfa,
            time_to_transcript_ms=ttft,
            total_turn_ms=total_ms,
            passed=bool(response_transcript),
            input_recording_path=input_rec_path,
            output_recording_path=output_rec_path,
        )


class SwarmHeadlessSessionClient:
    """
    Directly drives VoiceBrainSession in-process with genuine synthetic audio WAV bytes.
    Enables instant regression testing of the full reasoning, tool-calling, and safety pipeline
    without requiring active network ports.
    """

    def __init__(self, vault_dir: Optional[str] = None):
        import telegram_voice_caller

        self.vault_dir = vault_dir or os.environ.get("VAULT_DIR") or str(REPO_ROOT / "staging" / "vault")
        Path(self.vault_dir).mkdir(parents=True, exist_ok=True)
        self.brain = telegram_voice_caller.VoiceBrainSession(vault_dir=self.vault_dir)

    async def execute_audio_turn(
        self,
        text: str,
        persona: Persona,
        recordings_dir: Optional[Path] = None,
        turn_id: str = "turn",
    ) -> SwarmAudioTurnResult:
        start_total = time.monotonic()
        # Synthesize real speech
        pcm_16k = await synthesize_persona_speech(text, persona, sample_rate=16000)
        sent_duration_sec = len(pcm_16k) / (16000 * 2)
        wav_bytes = pcm_to_wav_bytes(pcm_16k, sample_rate=16000)

        input_rec_path = None
        if recordings_dir:
            input_rec_path = save_pcm_to_wav_file(
                pcm_16k,
                recordings_dir / f"{turn_id}_{persona.name}_in.wav",
                sample_rate=16000,
            )

        start_brain = time.monotonic()
        try:
            reply = await asyncio.to_thread(self.brain.process_user_turn, wav_bytes=wav_bytes, text_prompt=text)
            brain_duration_ms = (time.monotonic() - start_brain) * 1000.0
            passed = bool(reply)
            err_msg = ""
        except Exception as exc:
            reply = f"EXCEPTION: {type(exc).__name__}: {exc}"
            brain_duration_ms = (time.monotonic() - start_brain) * 1000.0
            passed = False
            err_msg = str(exc)

        # Inspect latest actions log from brain
        tools_invoked = []
        if self.brain.actions_log:
            tools_invoked = list(self.brain.actions_log[-3:])

        total_ms = (time.monotonic() - start_total) * 1000.0

        return SwarmAudioTurnResult(
            persona_name=persona.name,
            spoken_text=text,
            audio_sent_bytes=len(pcm_16k),
            audio_sent_duration_sec=sent_duration_sec,
            response_transcript=reply,
            time_to_transcript_ms=brain_duration_ms,
            total_turn_ms=total_ms,
            tools_invoked=tools_invoked,
            passed=passed,
            error_message=err_msg,
            input_recording_path=input_rec_path,
        )
