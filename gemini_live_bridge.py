#!/usr/bin/env python3
"""
gemini_live_bridge.py — Native Gemini 3.8 Live API Audio Bridge for Aethelgard Telegram Caller
=============================================================================================
Connects Telegram 1-on-1 VoIP audio (48kHz PCM) directly to Google Gemini 3.8 Live
over full-duplex WebSockets with:
  - Sub-second (~300ms) latency end-to-end neural speech
  - Cloud-native VAD & instantaneous barge-in (interruption handling)
  - Autonomous tool execution (save_gmail_draft, check_gmail, search_vault, end_call)
  - 15-minute safety call timeout with 14-minute warning
  - "Hang Up & Notify" error recovery
"""

import array
import asyncio
import io
import json
import logging
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from google import genai
from google.genai import types

logger = logging.getLogger("gemini_live_bridge")

# Sample rates
TELEGRAM_SAMPLE_RATE = 48000
GEMINI_INPUT_SAMPLE_RATE = 16000
GEMINI_OUTPUT_SAMPLE_RATE = 24000
FRAME_MS = 10
TELEGRAM_FRAME_BYTES = (TELEGRAM_SAMPLE_RATE * 2 * FRAME_MS) // 1000  # 960 bytes (NTgCalls strictly requires 10ms frames)


def downsample_48k_to_16k(pcm_48k: bytes) -> bytes:
    """Downsamples 48kHz mono s16le PCM to 16kHz using 3-tap averaging."""
    if not pcm_48k or len(pcm_48k) < 6:
        return b""
    usable = len(pcm_48k) - (len(pcm_48k) % 6)
    src = array.array("h")
    src.frombytes(pcm_48k[:usable])
    if sys.byteorder != "little":
        src.byteswap()
    dst = array.array("h", ((src[i] + src[i + 1] + src[i + 2]) // 3 for i in range(0, len(src), 3)))
    if sys.byteorder != "little":
        dst.byteswap()
    return dst.tobytes()


def pcm16_rms(pcm_bytes: bytes) -> float:
    """Computes Root Mean Square (energy) of raw 16-bit PCM bytes."""
    if not pcm_bytes:
        return 0.0
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    if usable <= 0:
        return 0.0
    s = array.array("h")
    s.frombytes(pcm_bytes[:usable])
    if sys.byteorder != "little":
        s.byteswap()
    sum_sq = sum(sample * sample for sample in s)
    return (sum_sq / len(s)) ** 0.5


def upsample_24k_to_48k(pcm_24k: bytes) -> bytes:
    """Upsamples 24kHz mono s16le PCM to 48kHz using linear sample interpolation."""
    if not pcm_24k or len(pcm_24k) < 2:
        return b""
    usable = len(pcm_24k) - (len(pcm_24k) % 2)
    src = array.array("h")
    src.frombytes(pcm_24k[:usable])
    if sys.byteorder != "little":
        src.byteswap()
    n = len(src)
    dst = array.array("h")
    for i in range(n - 1):
        dst.append(src[i])
        dst.append((src[i] + src[i + 1]) // 2)
    if n > 0:
        dst.append(src[-1])
        dst.append(src[-1])
    if sys.byteorder != "little":
        dst.byteswap()
    return dst.tobytes()


def get_live_tools() -> List[types.Tool]:
    """Builds the function declarations exposed to Gemini 3.8 Live."""
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="save_gmail_draft",
                    description="Creates and saves a draft email directly in Omar's Gmail Drafts folder. Call this immediately whenever Omar asks to write, draft, or compose an email.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "to": types.Schema(type=types.Type.STRING, description="Recipient email address (defaults to Omar if unspecified)"),
                            "subject": types.Schema(type=types.Type.STRING, description="Descriptive email subject line"),
                            "body": types.Schema(type=types.Type.STRING, description="Complete email body content in polite, professional formatting"),
                        },
                        required=["subject", "body"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="check_gmail",
                    description="Searches Omar's Gmail messages or lists recent emails with sender, subject, and snippet preview.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="Optional search query or sender name"),
                            "latest": types.Schema(type=types.Type.INTEGER, description="Number of recent emails to retrieve (1-5, default 3)"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="read_email",
                    description="Fetches the full text content of a specific Gmail message by ID.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "msg_id": types.Schema(type=types.Type.STRING, description="Gmail message ID from check_gmail"),
                        },
                        required=["msg_id"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="check_agenda",
                    description="Retrieves Omar's upcoming calendar events, work shifts, and Damascus airport weather.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "hours_ahead": types.Schema(type=types.Type.INTEGER, description="Lookahead window in hours (default 24)"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="search_vault",
                    description="Searches Omar's Obsidian knowledge vault for operational notes, flight logs, or memories.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="Topic, aircraft term, or keyword"),
                            "note_name": types.Schema(type=types.Type.STRING, description="Optional specific note file name"),
                        },
                        required=["query"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="append_vault_note",
                    description="Appends a quick dated observation or log entry to Omar's Obsidian vault notes.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "content": types.Schema(type=types.Type.STRING, description="Text to append"),
                            "note_name": types.Schema(type=types.Type.STRING, description="Note filename (defaults to Daily Log.md)"),
                        },
                        required=["content"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="end_call",
                    description="Terminates the live Telegram call after speaking a brief polite farewell to Omar.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "farewell": types.Schema(type=types.Type.STRING, description="Courteous closing remark, e.g. 'Goodbye Omar. Have a productive day.'"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
            ]
        ),
        types.Tool(google_search=types.GoogleSearch()),
    ]


class GeminiLiveBridgeSession:
    """
    Manages an active real-time bidirectional audio session between Telegram VoIP and Gemini 3.8 Live.
    """

    def __init__(
        self,
        peer_id: int,
        call_ctx: Dict[str, Any],
        brain: Any,
        log_cb: Optional[Callable[[str], None]] = None,
        hangup_cb: Optional[Callable[[], Any]] = None,
        notify_cb: Optional[Callable[[str], Any]] = None,
    ):
        self.peer_id = peer_id
        self.call_ctx = call_ctx
        self.brain = brain
        self.log_cb = log_cb or (lambda msg: None)
        self.hangup_cb = hangup_cb
        self.notify_cb = notify_cb

        self.session = None
        self.client = None
        self.is_connected = False
        self.is_closing = False
        self._receive_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._send_task: Optional[asyncio.Task] = None
        self._audio_buffer_48k = bytearray()
        self._start_mono = time.monotonic()
        self._warned_14m = False
        self._barge_streak_frames = 0

    def log(self, text: str) -> None:
        self.log_cb(f"[GeminiLive] {text}")

    async def start(self) -> bool:
        """Connects to Gemini 3.8 Live via Vertex AI with cached GCP OAuth credentials."""
        try:
            import antigravity_proxy as ap
            token = ap.get_valid_access_token()
            project = ap.VERTEX_PROJECT
            if not token or not project:
                self.log("ERROR: Missing Google OAuth token or GCP project")
                return False

            from google.oauth2.credentials import Credentials
            creds = Credentials(token)
            self.client = genai.Client(vertexai=True, project=project, location="us-central1", credentials=creds)

            system_instruction = (
                "You are Aethelgard, Omar's refined, loyal, and highly capable British butler custodian. "
                "You are speaking with Omar on a live Telegram voice call. "
                "Rules of conversation: "
                "1. Address him courteously as Omar or Sir. "
                "2. Keep spoken replies brief, crisp, and natural (1 to 2 sentences per response unless details are requested). "
                "3. Never recite code, raw JSON, or long markdown tables over voice. "
                "4. When Omar asks to draft or compose an email, IMMEDIATELY call `save_gmail_draft` without hesitating. "
                "5. If Omar speaks in Syrian/Levantine Arabic, respond fluently in Syrian Arabic. Otherwise, speak in British English. "
                "6. When Omar says 'goodbye', 'end the call', 'hang up', or 'talk later', call `end_call` and say farewell."
            )

            config = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                    )
                ),
                system_instruction=types.Content(
                    parts=[types.Part(text=system_instruction)]
                ),
                tools=get_live_tools(),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
            )

            self.log(f"Connecting to gemini-3.8-live on Vertex AI (project={project})...")
            # Connect session via async context manager protocol
            self._live_cm = self.client.aio.live.connect(model="gemini-3.8-live", config=config)
            self.session = await self._live_cm.__aenter__()
            self.is_connected = True
            self.log("Connected to Gemini 3.8 Live WebSocket successfully!")

            # Spawn receiver and sender background loops
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._send_task = asyncio.create_task(self._send_audio_loop())

            # Send opening greeting prompt to trigger immediate spoken greeting
            opening = self.call_ctx.get("opening_prompt") or "Hello Omar, Aethelgard online and at your service."
            await self.session.send_realtime_input(text=f"(Omar just connected the call. Greet him briefly: '{opening}')")

            return True
        except Exception as exc:
            self.log(f"Failed to connect to Gemini Live: {type(exc).__name__}: {exc}")
            if self.notify_cb:
                asyncio.create_task(self.notify_cb(f"Live Call error during connect: {exc}"))
            return False

    def feed_incoming_frame_48k(self, pcm_48k: bytes) -> None:
        """Called whenever a 48kHz audio frame arrives from Omar's Telegram microphone."""
        if not self.is_connected or self.is_closing:
            return

        # Acoustic Echo Suppression:
        # When Aethelgard is speaking out of Omar's phone speaker,
        # the phone microphone unavoidably picks up the acoustic bleed (~100-350 RMS).
        # We must suppress this speaker bleed while ai_speaking is True, otherwise Gemini Live's
        # cloud VAD hears its own voice, falsely flags an interruption, cuts itself off mid-sentence,
        # and gets trapped in an echo loop saying "I am listening, go ahead".
        # We only permit audio during playback if the caller speaks loudly (> 520 RMS)
        # for multiple consecutive frames (> 200ms) to indicate an intentional human barge-in.
        ai_speaking = bool(self.call_ctx.get("ai_speaking", False))
        if ai_speaking:
            rms = pcm16_rms(pcm_48k)
            if rms < 520.0:
                self._barge_streak_frames = 0
                return  # Drop speaker echo
            self._barge_streak_frames += 1
            if self._barge_streak_frames < 20:  # Require at least ~200ms sustained loud speech
                return
        else:
            self._barge_streak_frames = 0

        # Downsample 48kHz -> 16kHz PCM
        pcm_16k = downsample_48k_to_16k(pcm_48k)
        if pcm_16k:
            try:
                self._send_queue.put_nowait(pcm_16k)
            except Exception:
                pass

    async def _send_audio_loop(self) -> None:
        """Pumps downsampled 16kHz audio from Telegram into Gemini Live WebSocket."""
        try:
            while self.is_connected and not self.is_closing:
                # Batch 30ms to 60ms (960 to 1920 bytes) to prevent socket flooding and ensure smooth VAD
                chunk = await self._send_queue.get()
                buf = bytearray(chunk)
                while len(buf) < 960 and not self.is_closing:
                    try:
                        next_chunk = await asyncio.wait_for(self._send_queue.get(), timeout=0.035)
                        buf.extend(next_chunk)
                    except asyncio.TimeoutError:
                        break
                    except Exception:
                        break

                # Check 15-minute call duration cap
                elapsed = time.monotonic() - self._start_mono
                if elapsed > 840.0 and not self._warned_14m:  # 14 minutes
                    self._warned_14m = True
                    self.log("Approaching 15-minute call safety cap (14m elapsed)")
                    try:
                        await self.session.send_realtime_input(
                            text="(System reminder: Our operational voice link will reach its 15-minute safety threshold in one minute.)"
                        )
                    except Exception:
                        pass
                elif elapsed > 900.0:  # 15 minutes hard stop
                    self.log("Reached 15-minute safety cap; initiating graceful hangup")
                    try:
                        await self.session.send_realtime_input(
                            text="(System command: 15-minute call limit reached. Say a polite farewell to Omar and call end_call.)"
                        )
                    except Exception:
                        pass

                await self.session.send_realtime_input(
                    audio=types.Blob(data=bytes(buf), mime_type="audio/pcm;rate=16000")
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log(f"Send audio error: {exc}")

    async def _receive_loop(self) -> None:
        """Receives audio chunks, transcripts, interruptions, and tool calls from Gemini Live."""
        try:
            async for resp in self.session.receive():
                if self.is_closing:
                    break

                sc = resp.server_content
                if sc is not None:
                    # 1. Spoken audio response from Gemini
                    if sc.model_turn is not None:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                pcm_24k = part.inline_data.data
                                pcm_48k = upsample_24k_to_48k(pcm_24k)
                                self._enqueue_telegram_audio(pcm_48k)

                    # 2. Output and Input Transcripts
                    if sc.output_transcription and sc.output_transcription.text:
                        txt = sc.output_transcription.text.strip()
                        if txt:
                            self.log(f"Aethelgard: {txt}")
                            if self.brain and hasattr(self.brain, "transcript_log"):
                                self.brain.transcript_log.append(f"**Aethelgard:** {txt}")

                    if sc.input_transcription and sc.input_transcription.text:
                        u_txt = sc.input_transcription.text.strip()
                        if u_txt:
                            self.log(f"Omar: {u_txt}")
                            if self.brain and hasattr(self.brain, "transcript_log"):
                                self.brain.transcript_log.append(f"**Omar:** {u_txt}")

                    # 3. Interruption / Barge-in (Omar spoke while Aethelgard was speaking)
                    if sc.interrupted:
                        self.log("Cloud VAD: Interruption detected! Purging playback audio queue.")
                        self._purge_telegram_audio()

                    if sc.turn_complete:
                        self.log("Gemini model turn complete.")

                # 4. Tool Calls
                tc = resp.tool_call
                if tc is not None and tc.function_calls:
                    for call in tc.function_calls:
                        c_name = str(call.name or "")
                        c_args = call.args or {}
                        self.log(f"Tool Call received: {c_name} args={c_args}")

                        # Execute tool using VoiceBrainSession's proven execution dispatcher
                        tool_res = {}
                        try:
                            if self.brain and hasattr(self.brain, "execute_tool_call"):
                                tool_res = await asyncio.to_thread(
                                    self.brain.execute_tool_call, c_name, c_args
                                )
                            else:
                                tool_res = {"status": "ok"}
                        except Exception as tex:
                            self.log(f"Tool execution error on {c_name}: {tex}")
                            tool_res = {"error": str(tex)}

                        self.log(f"Tool Result for {c_name}: {str(tool_res)[:120]}")

                        # Send function response back to Gemini Live
                        f_resp = types.FunctionResponse(
                            id=call.id,
                            name=call.name,
                            response=tool_res,
                        )
                        await self.session.send_tool_response(function_responses=[f_resp])

                        # If tool was end_call, schedule graceful hangup after farewell audio drains
                        if c_name == "end_call":
                            self.log("end_call requested by model; scheduling hangup after farewell drain")
                            asyncio.create_task(self._hangup_after_delay(delay=3.5))

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log(f"Live receive loop error: {type(exc).__name__}: {exc}")
            # Follow agreed Hang Up & Notify protocol
            if not self.is_closing:
                if self.notify_cb:
                    asyncio.create_task(
                        self.notify_cb(f"Aethelgard Voice Call dropped due to connection issue: {exc}")
                    )
                if self.hangup_cb:
                    asyncio.create_task(self.hangup_cb())

    def _enqueue_telegram_audio(self, pcm_48k: bytes) -> None:
        """Buffers 48kHz audio and pushes standard 20ms (1920-byte) frames to Telegram out_queue."""
        self._audio_buffer_48k.extend(pcm_48k)
        out_q: asyncio.Queue = self.call_ctx.get("out_queue")
        if out_q is None:
            return

        while len(self._audio_buffer_48k) >= TELEGRAM_FRAME_BYTES:
            frame = bytes(self._audio_buffer_48k[:TELEGRAM_FRAME_BYTES])
            del self._audio_buffer_48k[:TELEGRAM_FRAME_BYTES]
            try:
                out_q.put_nowait(frame)
            except Exception:
                break

    def _purge_telegram_audio(self) -> None:
        """Purges any pending playback audio in Telegram's output queue for instant barge-in."""
        self._audio_buffer_48k.clear()
        if self.call_ctx:
            self.call_ctx["ai_speaking"] = False
        out_q: asyncio.Queue = self.call_ctx.get("out_queue")
        if out_q is not None:
            while not out_q.empty():
                try:
                    out_q.get_nowait()
                except Exception:
                    break

    async def _hangup_after_delay(self, delay: float = 3.5) -> None:
        """Allows farewell audio to play on Omar's phone before hanging up."""
        await asyncio.sleep(delay)
        if self.hangup_cb and not self.is_closing:
            await self.hangup_cb()

    async def close(self) -> None:
        """Gracefully closes Gemini Live WebSocket session and cancels background tasks."""
        if self.is_closing:
            return
        self.is_closing = True
        self.is_connected = False
        self.log("Closing Gemini Live session...")

        if self._receive_task:
            self._receive_task.cancel()
        if self._send_task:
            self._send_task.cancel()

        if self.session is not None and hasattr(self, "_live_cm"):
            try:
                await self._live_cm.__aexit__(None, None, None)
            except Exception:
                pass
        self.log("Gemini Live session closed.")
