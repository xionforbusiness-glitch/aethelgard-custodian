#!/usr/bin/env python3
"""
Aethelgard Native 1-on-1 Telegram Voice Caller (+20 MTProto P2P Bridge)
=======================================================================
Rigs a dedicated Telegram personal session (Telethon MTProto + NTgCalls WebRTC)
into an encrypted 1-on-1 audio bridge with Omar's Telegram account:
  - Whitelisted strictly to `TELEGRAM_ALLOWED_USERS` (unknown callers auto-rejected).
  - Full-duplex 48 kHz <-> 16 kHz PCM pipeline with pure-Python RMS VAD & barge-in.
  - Powered 100% by Antigravity Pro OAuth (`gemini-3.8-flash-low`) + Edge-TTS Jarvis voice.
  - Supports real-time tool execution & strict Two-Step Verbal Approval Readback.
  - Exposes local RPC on `127.0.0.1:20132` so proactive triggers share one session.
"""

import array
import asyncio
import base64
import datetime
import hashlib
import io
import json
import math
import os
import random
import re
import sys
import threading
import time
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from telethon import TelegramClient, errors, events
    from telethon.tl import functions, types
    TELETHON_AVAILABLE = True
except Exception:
    TelegramClient = None  # type: ignore
    errors = None  # type: ignore
    events = None  # type: ignore
    functions = None  # type: ignore
    types = None  # type: ignore
    TELETHON_AVAILABLE = False

try:
    import qrcode
    QRCODE_AVAILABLE = True
except Exception:
    qrcode = None  # type: ignore
    QRCODE_AVAILABLE = False

try:
    import ntgcalls
    NTGCALLS_AVAILABLE = True
except Exception:
    ntgcalls = None  # type: ignore
    NTGCALLS_AVAILABLE = False

CALLER_DEPS_AVAILABLE = TELETHON_AVAILABLE and NTGCALLS_AVAILABLE

RPC_HOST = "127.0.0.1"
RPC_PORT = int(os.environ.get("PORT_TELEGRAM_CALLER_RPC", 20132))
PROXY_URL = os.environ.get("OMNIROUTE_URL", "http://127.0.0.1:20128/v1/chat/completions")
MODEL_NAME = os.environ.get("VOICE_CALL_MODEL", "antigravity/gemini-3.8-flash-low")

VOICE_SAMPLE_RATE = 48000
ASR_SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_BYTES_48K = (VOICE_SAMPLE_RATE * 2 * FRAME_MS) // 1000  # 1920 bytes


# ==============================================================================
# 1. CREDENTIALS & STRICT WHITELIST GUARD
# ==============================================================================

def _get_credentials_dir() -> Path:
    override = os.environ.get("AETHELGARD_CREDENTIALS_DIR")
    if override:
        p = Path(os.path.expanduser(override))
    else:
        home = os.environ.get("AETHELGARD_HOME")
        base = Path(os.path.expanduser(home)) if home else (Path.home() / ".aethelgard")
        p = base / "credentials"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_caller_config_path() -> Path:
    return _get_credentials_dir() / "telegram_caller.json"


def get_caller_session_path() -> Path:
    return _get_credentials_dir() / "aethelgard_caller.session"


def load_caller_config() -> Dict[str, Any]:
    path = get_caller_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_caller_config(api_id: int, api_hash: str, phone: str, target_user_id: Optional[str] = None) -> Dict[str, Any]:
    cfg = load_caller_config()
    cfg.update({
        "api_id": int(api_id),
        "api_hash": api_hash.strip(),
        "phone": phone.strip(),
        "target_user_id": str(target_user_id or os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")).split(",")[0].strip(),
        "session_path": str(get_caller_session_path()),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    path = get_caller_config_path()
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def get_allowed_caller_ids() -> Set[int]:
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")
    ids: Set[int] = set()
    for part in str(raw).split(","):
        clean = part.strip()
        if clean.lstrip("-").isdigit():
            ids.add(int(clean))
    if not ids:
        ids.add(1021125594)
    return ids


def is_caller_authorized(user_id: Any) -> bool:
    """Strictly authorizes only Omar's whitelisted Telegram numeric user ID(s)."""
    try:
        uid = int(user_id)
    except Exception:
        return False
    return uid in get_allowed_caller_ids()


def get_caller_status() -> Dict[str, Any]:
    cfg = load_caller_config()
    session_file = Path(cfg.get("session_path") or get_caller_session_path())
    # Telethon adds .session suffix if omitted; check both
    session_exists = session_file.exists() or Path(str(session_file) + ".session").exists()
    configured = bool(cfg.get("api_id") and cfg.get("api_hash") and session_exists)
    return {
        "deps_available": CALLER_DEPS_AVAILABLE,
        "telethon_available": TELETHON_AVAILABLE,
        "ntgcalls_available": NTGCALLS_AVAILABLE,
        "configured": configured,
        "status": "connected" if (CALLER_DEPS_AVAILABLE and configured) else ("deps_missing" if not CALLER_DEPS_AVAILABLE else "needs_auth"),
        "allowed_users": sorted(get_allowed_caller_ids()),
        "phone_masked": (cfg.get("phone", "")[:4] + "***") if cfg.get("phone") else "",
        "rpc_endpoint": f"http://{RPC_HOST}:{RPC_PORT}",
    }


# ==============================================================================
# 2. PURE-PYTHON PCM DSP, VAD & BARGE-IN DETECTOR
# ==============================================================================

def pcm16_rms(pcm_bytes: bytes) -> float:
    """Computes RMS amplitude of signed 16-bit little-endian PCM bytes."""
    if not pcm_bytes or len(pcm_bytes) < 2:
        return 0.0
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    samples = array.array("h")
    samples.frombytes(pcm_bytes[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0
    acc = sum(s * s for s in samples)
    return math.sqrt(acc / len(samples))


def downsample_48k_to_16k(pcm_48k: bytes) -> bytes:
    """Downsamples 48kHz mono s16le PCM to 16kHz using a 3-tap averaging filter."""
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


def pcm16_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = ASR_SAMPLE_RATE, channels: int = 1) -> bytes:
    """Wraps raw s16le PCM audio in a standard RIFF WAV header in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class AudioTurnDetector:
    """
    Rolling-energy Voice Activity Detector & Barge-In controller for 48kHz 20ms PCM frames.
    Emits:
      - `barge_in=True` once uninterrupted user speech exceeds `barge_in_ms` while AI is speaking
      - `utterance_ready=True` + `utterance_pcm_16k` when speech finishes after `silence_end_ms`
    """

    def __init__(
        self,
        speech_rms_threshold: float = 550.0,
        frame_ms: int = 20,
        barge_in_ms: int = 220,
        min_speech_ms: int = 260,
        silence_end_ms: int = 840,
        max_turn_ms: int = 18000,
    ):
        self.speech_rms_threshold = float(speech_rms_threshold)
        self.frame_ms = int(frame_ms)
        self.barge_in_ms = int(barge_in_ms)
        self.min_speech_ms = int(min_speech_ms)
        self.silence_end_ms = int(silence_end_ms)
        self.max_turn_ms = int(max_turn_ms)

        self.in_speech = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.total_turn_ms = 0
        self._barge_streak_ms = 0
        self._barge_fired = False
        self._buffer_48k = bytearray()

    def reset(self) -> None:
        self.in_speech = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.total_turn_ms = 0
        self._barge_streak_ms = 0
        self._barge_fired = False
        self._buffer_48k.clear()

    def feed_frame(self, pcm_48k: bytes, ai_speaking: bool = False) -> Dict[str, Any]:
        rms = pcm16_rms(pcm_48k)
        is_voice = rms >= self.speech_rms_threshold
        barge_in = False
        utterance_ready = False
        utterance_pcm_16k: Optional[bytes] = None

        if ai_speaking:
            if is_voice:
                self._barge_streak_ms += self.frame_ms
                if self._barge_streak_ms >= self.barge_in_ms and not self._barge_fired:
                    self._barge_fired = True
                    barge_in = True
            else:
                self._barge_streak_ms = 0
                self._barge_fired = False
        else:
            self._barge_streak_ms = 0
            self._barge_fired = False

        if is_voice:
            self.in_speech = True
            self.speech_ms += self.frame_ms
            self.silence_ms = 0
            self.total_turn_ms += self.frame_ms
            self._buffer_48k.extend(pcm_48k)
        elif self.in_speech:
            self.silence_ms += self.frame_ms
            self.total_turn_ms += self.frame_ms
            self._buffer_48k.extend(pcm_48k)

            if self.silence_ms >= self.silence_end_ms or self.total_turn_ms >= self.max_turn_ms:
                if self.speech_ms >= self.min_speech_ms and len(self._buffer_48k) > 0:
                    utterance_pcm_16k = downsample_48k_to_16k(bytes(self._buffer_48k))
                    utterance_ready = bool(utterance_pcm_16k)
                self.reset()

        return {
            "rms": round(rms, 1),
            "is_voice": is_voice,
            "barge_in": barge_in,
            "utterance_ready": utterance_ready,
            "utterance_pcm_16k": utterance_pcm_16k,
        }


# ==============================================================================
# 3. TWO-STEP VERBAL APPROVAL GUARD
# ==============================================================================

class VerbalApprovalGuard:
    """
    Prevents blind or hallucinated outbound dispatch on a live voice call.
    Requires:
      1. The draft was explicitly staged & registered for verbal readback on this call.
      2. Omar speaks an explicit confirmation phrase without negation/cancellation words.
    """

    _CONFIRM_RE = re.compile(
        r"\b(confirm|approve|send it|go ahead|yes send|execute|transmit)\b|(?:نفذ|ابعت|أرسل|ارسل|موافق|اكد|تأكيد)",
        re.IGNORECASE,
    )
    _NEGATION_RE = re.compile(
        r"\b(no|cancel|stop|wait|hold|don't|do not|reject|discard)\b|(?:لا\b|الغي|إلغاء|استنى|بلاش|توقف)",
        re.IGNORECASE,
    )

    def __init__(self):
        self.readback_draft_ids: Set[str] = set()

    def register_readback(self, draft_id: str) -> None:
        if draft_id:
            self.readback_draft_ids.add(draft_id.strip())

    def is_explicit_confirm(self, spoken_confirmation: str) -> bool:
        text = (spoken_confirmation or "").strip()
        if not text:
            return False
        if self._NEGATION_RE.search(text):
            return False
        return bool(self._CONFIRM_RE.search(text))

    def authorize(self, draft_id: str, spoken_confirmation: str) -> Dict[str, Any]:
        import approval_gatekeeper

        clean_id = (draft_id or "").strip()
        if clean_id not in self.readback_draft_ids:
            return {
                "success": False,
                "error": f"Blocked by Two-Step Verbal Guard: draft '{clean_id}' must be read back to Omar first.",
            }
        if not self.is_explicit_confirm(spoken_confirmation):
            return {
                "success": False,
                "error": "Blocked by Two-Step Verbal Guard: explicit affirmative confirmation phrase required.",
            }
        return approval_gatekeeper.approve_draft(
            clean_id,
            execute=True,
            approved_via="telegram_voice_call",
        )


# ==============================================================================
# 4. ANTIGRAVITY MULTIMODAL BRAIN + TOOL CALLS
# ==============================================================================

VOICE_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_gmail",
            "description": "Search or read Omar's latest Gmail messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional Gmail search query"},
                    "latest": {"type": "integer", "description": "Number of latest emails (1-5)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_agenda",
            "description": "Get Omar's upcoming shifts, Google Calendar .ics events, and aviation runway alerts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_ahead": {"type": "integer", "description": "Lookahead window in hours"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_promises",
            "description": "List open commitments and follow-ups tracked in the Promise Ledger.",
            "parameters": {
                "type": "object",
                "properties": {
                    "overdue_only": {"type": "boolean", "description": "Only return overdue commitments"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stage_outbound_draft",
            "description": "Stage an outbound email/message draft safely in Gatekeeper without sending. Must be verbally read back before confirm_verbal_approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "description": "gmail, whatsapp, telegram, or linkedin"},
                    "to": {"type": "string", "description": "Recipient email, handle, or visibility"},
                    "subject": {"type": "string", "description": "Optional email subject"},
                    "body": {"type": "string", "description": "Exact draft content"},
                },
                "required": ["platform", "to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_verbal_approval",
            "description": "Execute a previously staged draft ONLY after verbally reading it back and receiving Omar's explicit confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string", "description": "Staged draft ID to execute"},
                    "spoken_confirmation": {"type": "string", "description": "Exact confirmation words spoken by Omar"},
                },
                "required": ["draft_id", "spoken_confirmation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "queue_async_task",
            "description": "Queue a background task or reply watch and call Omar back when finished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "watch_reply, timed_callback, or hermes_task"},
                    "title": {"type": "string", "description": "Short task title"},
                    "detail": {"type": "string", "description": "Search query, ISO trigger time, or shell/task detail"},
                },
                "required": ["kind", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compile_life_update",
            "description": "Compile a spoken bilingual update into expenses, shift times, promises, and Obsidian Vault log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "transcript": {"type": "string", "description": "Exact life update text"},
                },
                "required": ["transcript"],
            },
        },
    },
]


class VoiceBrainSession:
    """Manages Antigravity multimodal turn execution, tool routing, and call transcript capture."""

    def __init__(self, vault_dir: Optional[str] = None, max_history: int = 12):
        self.vault_dir = vault_dir or os.environ.get("VAULT_DIR") or os.path.expanduser("~/vault")
        self.max_history = max_history
        self.history: List[Dict[str, Any]] = []
        self.transcript_log: List[str] = []
        self.approval_guard = VerbalApprovalGuard()

    def build_system_prompt(self) -> str:
        op_mode = {"mode": "STRATEGIC_WINDOW", "irops_alert": False}
        agenda_txt = ""
        pending_count = 0
        try:
            import circadian_oracle
            op_mode = circadian_oracle.get_operational_mode(vault_dir=self.vault_dir)
        except Exception:
            pass
        try:
            import calendar_bridge
            agenda_txt = calendar_bridge.format_agenda_summary(hours_ahead=24)
        except Exception:
            pass
        try:
            import approval_gatekeeper
            pending_count = len(approval_gatekeeper.list_pending_drafts())
        except Exception:
            pass

        brevity = (
            "Omar is on COCKPIT_DUTY shift — keep answers ultra-brief (1-2 short sentences)."
            if op_mode.get("mode") == "COCKPIT_DUTY"
            else "Keep spoken replies natural, crisp, and warm (1-3 sentences) so audio synthesis stays fast."
        )
        return (
            "You are Aethelgard, Omar Elnemr's personal JARVIS-grade voice executive on a live 1-on-1 Telegram voice call.\n"
            f"- Operational Mode: {op_mode.get('mode')} (OSDI IROPS: {op_mode.get('irops_alert')})\n"
            f"- Pending Outbound Approvals: {pending_count}\n"
            f"- Upcoming Agenda:\n{agenda_txt or 'No immediate agenda items'}\n"
            "- Language: Match Omar seamlessly (English in crisp British JARVIS tone, or natural Syrian Arabic if he speaks Arabic).\n"
            f"- Delivery: {brevity} Never read raw Markdown symbols, backticks, or JSON aloud.\n"
            "- Outbound Safety: NEVER send external emails/messages silently. Stage via `stage_outbound_draft`, "
            "read recipient + subject + summary aloud, and call `confirm_verbal_approval` ONLY in a subsequent turn after Omar says confirm/send."
        )

    def execute_tool_call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if name == "check_gmail":
                from omnichannel_bridge import GoogleAdapter
                ga = GoogleAdapter()
                query = str(args.get("query") or "").strip()
                latest = min(int(args.get("latest") or 3), 5)
                if query:
                    return {"emails": ga.search_emails(query=query, max_results=latest)}
                return {"emails": ga.get_latest_emails(count=latest)}

            if name == "check_agenda":
                import calendar_bridge
                hrs = int(args.get("hours_ahead") or 24)
                return calendar_bridge.get_unified_agenda(hours_ahead=hrs)

            if name == "list_promises":
                import promise_ledger
                if args.get("overdue_only"):
                    return {"overdue": promise_ledger.check_overdue_promises()}
                return {"open": promise_ledger.list_promises(status="open")[:8]}

            if name == "stage_outbound_draft":
                import approval_gatekeeper
                platform = str(args.get("platform") or "gmail").lower()
                draft = approval_gatekeeper.stage_draft(
                    platform=platform,
                    action="send_email" if platform in ("gmail", "google") else "send_message",
                    recipient_or_target=str(args.get("to") or ""),
                    content=str(args.get("body") or ""),
                    subject=str(args.get("subject") or "") or None,
                    notify_telegram=True,
                )
                self.approval_guard.register_readback(draft["draft_id"])
                return {"staged": True, "draft_id": draft["draft_id"], "readback_required": True}

            if name == "confirm_verbal_approval":
                return self.approval_guard.authorize(
                    draft_id=str(args.get("draft_id") or ""),
                    spoken_confirmation=str(args.get("spoken_confirmation") or ""),
                )

            if name == "queue_async_task":
                import async_callback_engine
                kind = str(args.get("kind") or "watch_reply")
                title = str(args.get("title") or "Background task")
                detail = str(args.get("detail") or "")
                payload: Dict[str, Any] = {}
                if kind == "watch_reply":
                    payload = {"query": detail or title}
                elif kind == "timed_callback":
                    payload = {"trigger_at": detail, "message": title}
                else:
                    payload = {"command": detail}
                return async_callback_engine.enqueue_async_job(kind=kind, title=title, payload=payload, delivery="call")

            if name == "compile_life_update":
                import life_compiler
                return life_compiler.compile_life_dump(
                    str(args.get("transcript") or ""),
                    vault_dir=self.vault_dir,
                )

            return {"error": f"Unknown voice tool: {name}"}
        except Exception as exc:
            return {"error": str(exc)}

    def _post_proxy(self, messages: List[Dict[str, Any]], use_tools: bool = True) -> Dict[str, Any]:
        api_key = os.environ.get("OMNIROUTE_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "sk-omni")
        payload: Dict[str, Any] = {
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0.4,
        }
        if use_tools:
            payload["tools"] = VOICE_TOOLS
        req = urllib.request.Request(
            PROXY_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def process_user_turn(self, wav_bytes: Optional[bytes] = None, text_prompt: str = "") -> str:
        """
        Executes a live voice turn through Antigravity (`gemini-3.8-flash-low`) with real tool loops.
        Accepts raw WAV bytes (encoded via `input_audio`) and/or a text prompt.
        """
        content_parts: List[Dict[str, Any]] = []
        if wav_bytes:
            b64_wav = base64.b64encode(wav_bytes).decode("ascii")
            content_parts.append({
                "type": "text",
                "text": text_prompt or "Listen to Omar's spoken audio turn carefully and respond directly in the same language (English or Syrian Arabic).",
            })
            content_parts.append({
                "type": "input_audio",
                "input_audio": {"data": b64_wav, "format": "wav"},
            })
        elif text_prompt:
            content_parts.append({"type": "text", "text": text_prompt})
        else:
            return ""

        user_msg = {"role": "user", "content": content_parts}
        # Store lighter text marker in long-term history after turn finishes to avoid bloating base64 payloads
        conv_messages = [{"role": "system", "content": self.build_system_prompt()}] + self.history[-self.max_history :] + [user_msg]

        reply_text = ""
        for _ in range(4):
            resp = self._post_proxy(conv_messages, use_tools=True)
            choice = (resp.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                conv_messages.append(msg)
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    t_name = str(fn.get("name") or "")
                    try:
                        t_args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        t_args = {}
                    t_res = self.execute_tool_call(t_name, t_args)
                    conv_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id") or f"call_{t_name}",
                        "content": json.dumps(t_res, ensure_ascii=False),
                    })
                continue
            reply_text = str(msg.get("content") or "").strip()
            break

        if not reply_text:
            reply_text = "I processed that for you, Omar."

        hist_user_label = text_prompt if text_prompt else "[Omar spoke via live voice]"
        self.history.append({"role": "user", "content": hist_user_label})
        self.history.append({"role": "assistant", "content": reply_text})
        self.history = self.history[-self.max_history :]

        self.transcript_log.append(f"**Omar:** {hist_user_label}")
        self.transcript_log.append(f"**Aethelgard:** {reply_text}")
        return reply_text

    def flush_transcript_to_vault(self) -> Optional[str]:
        if not self.transcript_log:
            return None
        vault_path = Path(os.path.expanduser(self.vault_dir))
        if not vault_path.exists():
            return None
        t_dir = vault_path / "raw" / "transcripts"
        t_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
        note = t_dir / f"{now.strftime('%Y-%m-%d')}-telegram-call.md"
        header = f"\n\n# 📞 Native Telegram Call Transcript ({now.strftime('%Y-%m-%d %H:%M Damascus')})\n\n"
        with open(note, "a", encoding="utf-8") as fh:
            fh.write(header + "\n\n".join(self.transcript_log) + "\n")
        return str(note)


# ==============================================================================
# 5. TELETHON + NTGCALLS P2P CALL RUNTIME BRIDGE
# ==============================================================================

class TelegramP2PCallManager:
    """
    Single-instance Telethon + NTgCalls manager handling incoming/outgoing 1-on-1 P2P calls
    exclusively for whitelisted `TELEGRAM_ALLOWED_USERS`.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.cfg = load_caller_config()
        self.client: Optional[Any] = None
        self.ntg: Optional[Any] = None
        self.active_calls: Dict[int, Dict[str, Any]] = {}  # peer_id -> call state
        self.call_by_tg_id: Dict[int, int] = {}            # phone_call.id -> peer_id

    async def start(self) -> bool:
        if not CALLER_DEPS_AVAILABLE:
            return False
        if not (self.cfg.get("api_id") and self.cfg.get("api_hash")):
            return False

        self.ntg = ntgcalls.NTgCalls()
        session_path = str(self.cfg.get("session_path") or get_caller_session_path())
        self.client = TelegramClient(
            session_path,
            int(self.cfg["api_id"]),
            self.cfg["api_hash"],
            loop=self.loop,
            device_model="Ubuntu Workstation",
            system_version="Linux 6.8",
            app_version="4.16.0",
        )
        await self.client.connect()
        if not await self.client.is_user_authorized():
            print("⚠ Telegram Caller session not authorized yet. Run `python telegram_voice_caller.py login` once.")
            return False

        self._register_ntg_callbacks()
        self._register_telethon_handlers()
        return True

    def _register_ntg_callbacks(self) -> None:
        assert self.ntg is not None

        def _on_signaling(peer_id: int, data: bytes) -> None:
            ctx = self.active_calls.get(peer_id)
            if not ctx or not self.client:
                return
            input_call = ctx.get("input_call")
            if not input_call:
                return
            asyncio.run_coroutine_threadsafe(
                self.client(functions.phone.SendSignalingDataRequest(peer=input_call, data=data)),
                self.loop,
            )

        def _on_frames(peer_id: int, mode: Any, device: Any, frames: list) -> None:
            ctx = self.active_calls.get(peer_id)
            if not ctx:
                return
            vad: AudioTurnDetector = ctx["vad"]
            for fr in frames or []:
                raw = bytes(getattr(fr, "data", b""))
                if not raw:
                    continue
                res = vad.feed_frame(raw, ai_speaking=ctx.get("ai_speaking", False))
                if res.get("barge_in"):
                    ctx["generation"] = ctx.get("generation", 0) + 1
                    ctx["ai_speaking"] = False
                    out_q: asyncio.Queue = ctx["out_queue"]
                    while not out_q.empty():
                        try:
                            out_q.get_nowait()
                        except Exception:
                            break
                if res.get("utterance_ready") and res.get("utterance_pcm_16k"):
                    pcm16 = res["utterance_pcm_16k"]
                    asyncio.run_coroutine_threadsafe(
                        self._handle_utterance(peer_id, pcm16),
                        self.loop,
                    )

        def _on_conn_change(peer_id: int, info: Any) -> None:
            ctx = self.active_calls.get(peer_id)
            if not ctx:
                return
            state = getattr(info, "state", None)
            if state == ntgcalls.ConnectionState.CONNECTED and not ctx.get("greeted"):
                ctx["greeted"] = True
                opening = ctx.get("opening_prompt") or "Hello Omar, Aethelgard online. Go ahead."
                asyncio.run_coroutine_threadsafe(self._speak_text(peer_id, opening), self.loop)
            elif state in (ntgcalls.ConnectionState.CLOSED, ntgcalls.ConnectionState.FAILED, ntgcalls.ConnectionState.TIMEOUT):
                asyncio.run_coroutine_threadsafe(self._cleanup_call(peer_id), self.loop)

        self.ntg.on_signaling_data(_on_signaling)
        self.ntg.on_frames(_on_frames)
        self.ntg.on_connection_change(_on_conn_change)

    def _register_telethon_handlers(self) -> None:
        assert self.client is not None

        @self.client.on(events.Raw)
        async def _raw_phone_handler(event):
            if isinstance(event, types.UpdatePhoneCallSignalingData):
                peer_id = self.call_by_tg_id.get(event.phone_call_id)
                if peer_id and self.ntg:
                    await self.ntg.send_signaling_data(peer_id, event.data)
            elif isinstance(event, types.UpdatePhoneCall):
                await self._on_phone_call_update(event.phone_call)

    async def _prepare_ntg_peer(self, peer_id: int) -> None:
        assert self.ntg is not None
        await self.ntg.create_p2p_call(peer_id)
        ad = ntgcalls.AudioDescription(ntgcalls.MediaSource.EXTERNAL, VOICE_SAMPLE_RATE, 1, "", False)
        await self.ntg.set_stream_sources(
            peer_id,
            ntgcalls.StreamMode.CAPTURE,
            ntgcalls.MediaDescription(microphone=ad, speaker=None, camera=None, screen=None),
        )
        await self.ntg.set_stream_sources(
            peer_id,
            ntgcalls.StreamMode.PLAYBACK,
            ntgcalls.MediaDescription(microphone=None, speaker=ad, camera=None, screen=None),
        )

    async def _connect_webrtc(self, peer_id: int, phone_call: Any) -> None:
        assert self.ntg is not None
        servers = [
            ntgcalls.RTCServer(
                id=c.id,
                ipv4=getattr(c, "ip", "") or "",
                ipv6=getattr(c, "ipv6", "") or "",
                port=c.port,
                username=getattr(c, "username", None),
                password=getattr(c, "password", None),
                turn=bool(getattr(c, "turn", False)),
                stun=bool(getattr(c, "stun", False)),
                tcp=False,
                peer_tag=getattr(c, "peer_tag", None),
            )
            for c in (getattr(phone_call, "connections", None) or [])
        ]
        proto = getattr(phone_call, "protocol", None)
        versions = list(getattr(proto, "library_versions", None) or self.ntg.get_protocol().library_versions)
        p2p_allowed = bool(getattr(phone_call, "p2p_allowed", True))
        custom_params = getattr(getattr(phone_call, "custom_parameters", None), "data", None)
        await self.ntg.connect_p2p(peer_id, servers, versions, p2p_allowed, custom_params)

    async def _on_phone_call_update(self, phone_call: Any) -> None:
        assert self.client is not None and self.ntg is not None

        if isinstance(phone_call, types.PhoneCallRequested):
            caller_id = int(phone_call.admin_id)
            if not is_caller_authorized(caller_id):
                await self.client(functions.phone.DiscardCallRequest(
                    peer=types.InputPhoneCall(id=phone_call.id, access_hash=phone_call.access_hash),
                    duration=0,
                    reason=types.PhoneCallDiscardReasonBusy(),
                    connection_id=0,
                ))
                return

            dh_res = await self.client(functions.messages.GetDhConfigRequest(version=0, random_length=256))
            dh_cfg = ntgcalls.DhConfig(dh_res.g, dh_res.p, os.urandom(256))
            await self._prepare_ntg_peer(caller_id)
            g_b = await self.ntg.init_exchange(caller_id, dh_cfg, phone_call.g_a_hash)

            input_call = types.InputPhoneCall(id=phone_call.id, access_hash=phone_call.access_hash)
            self._init_call_context(caller_id, input_call, phone_call.id, opening_prompt="Aethelgard listening, Omar. Go ahead.")

            ntg_proto = self.ntg.get_protocol()
            accepted = await self.client(functions.phone.AcceptCallRequest(
                peer=input_call,
                g_b=g_b,
                protocol=types.PhoneCallProtocol(
                    udp_p2p=ntg_proto.udp_p2p,
                    udp_reflector=ntg_proto.udp_reflector,
                    min_layer=ntg_proto.min_layer,
                    max_layer=ntg_proto.max_layer,
                    library_versions=ntg_proto.library_versions,
                ),
            ))
            await self._on_phone_call_update(accepted.phone_call)

        elif isinstance(phone_call, types.PhoneCallAccepted):
            peer_id = self.call_by_tg_id.get(phone_call.id)
            if not peer_id:
                return
            auth = await self.ntg.exchange_keys(peer_id, phone_call.g_b, 0)
            ctx = self.active_calls[peer_id]
            ntg_proto = self.ntg.get_protocol()
            confirmed = await self.client(functions.phone.ConfirmCallRequest(
                peer=ctx["input_call"],
                g_a=auth.g_a_or_b,
                key_fingerprint=auth.key_fingerprint,
                protocol=types.PhoneCallProtocol(
                    udp_p2p=ntg_proto.udp_p2p,
                    udp_reflector=ntg_proto.udp_reflector,
                    min_layer=ntg_proto.min_layer,
                    max_layer=ntg_proto.max_layer,
                    library_versions=ntg_proto.library_versions,
                ),
            ))
            await self._connect_webrtc(peer_id, confirmed.phone_call)

        elif isinstance(phone_call, types.PhoneCall):
            peer_id = self.call_by_tg_id.get(phone_call.id)
            if not peer_id:
                return
            await self.ntg.exchange_keys(peer_id, phone_call.g_a_or_b, phone_call.key_fingerprint)
            await self._connect_webrtc(peer_id, phone_call)

        elif isinstance(phone_call, types.PhoneCallDiscarded):
            peer_id = self.call_by_tg_id.get(phone_call.id)
            if peer_id:
                await self._cleanup_call(peer_id)

    def _init_call_context(
        self,
        peer_id: int,
        input_call: Any,
        tg_call_id: int,
        opening_prompt: str = "",
    ) -> Dict[str, Any]:
        ctx = {
            "peer_id": peer_id,
            "input_call": input_call,
            "tg_call_id": tg_call_id,
            "vad": AudioTurnDetector(),
            "brain": VoiceBrainSession(),
            "out_queue": asyncio.Queue(),
            "generation": 0,
            "ai_speaking": False,
            "greeted": False,
            "opening_prompt": opening_prompt,
            "turn_lock": asyncio.Lock(),
            "sender_task": None,
        }
        ctx["sender_task"] = self.loop.create_task(self._audio_sender_loop(peer_id, ctx))
        self.active_calls[peer_id] = ctx
        self.call_by_tg_id[tg_call_id] = peer_id
        return ctx

    async def initiate_outgoing_call(self, opening_prompt: str = "", target_user_id: Optional[int] = None) -> Dict[str, Any]:
        if not self.client or not self.ntg:
            return {"success": False, "error": "Telegram Caller client not connected"}

        uid = int(target_user_id or next(iter(sorted(get_allowed_caller_ids()))))
        if not is_caller_authorized(uid):
            return {"success": False, "error": "Target user is outside TELEGRAM_ALLOWED_USERS"}

        entity = await self.client.get_input_entity(uid)
        dh_res = await self.client(functions.messages.GetDhConfigRequest(version=0, random_length=256))
        dh_cfg = ntgcalls.DhConfig(dh_res.g, dh_res.p, os.urandom(256))
        await self._prepare_ntg_peer(uid)
        g_a_hash = await self.ntg.init_exchange(uid, dh_cfg, None)

        ntg_proto = self.ntg.get_protocol()
        res = await self.client(functions.phone.RequestCallRequest(
            user_id=entity,
            random_id=random.randint(1, 0x7FFFFFFF),
            g_a_hash=g_a_hash,
            protocol=types.PhoneCallProtocol(
                udp_p2p=ntg_proto.udp_p2p,
                udp_reflector=ntg_proto.udp_reflector,
                min_layer=ntg_proto.min_layer,
                max_layer=ntg_proto.max_layer,
                library_versions=ntg_proto.library_versions,
            ),
            video=False,
        ))
        call = res.phone_call
        input_call = types.InputPhoneCall(id=call.id, access_hash=call.access_hash)
        self._init_call_context(
            uid,
            input_call,
            call.id,
            opening_prompt=opening_prompt or "Omar, Aethelgard on the line. Go ahead whenever you're ready.",
        )
        return {"success": True, "call_id": call.id, "target_user_id": uid}

    async def _speak_text(self, peer_id: int, text: str) -> None:
        import voice_engine
        ctx = self.active_calls.get(peer_id)
        if not ctx or not text.strip():
            return
        gen_at_start = ctx["generation"]
        ctx["ai_speaking"] = True
        try:
            async for frame in voice_engine.synthesize_pcm_frames(text, voice="auto", sample_rate=VOICE_SAMPLE_RATE):
                if ctx.get("generation") != gen_at_start or peer_id not in self.active_calls:
                    break
                await ctx["out_queue"].put(frame)
        except Exception as exc:
            print(f"⚠ Live call TTS error: {exc}")

    async def _handle_utterance(self, peer_id: int, pcm_16k: bytes) -> None:
        ctx = self.active_calls.get(peer_id)
        if not ctx:
            return
        async with ctx["turn_lock"]:
            wav_bytes = pcm16_to_wav_bytes(pcm_16k, sample_rate=ASR_SAMPLE_RATE)
            brain: VoiceBrainSession = ctx["brain"]
            reply = await asyncio.to_thread(brain.process_user_turn, wav_bytes=wav_bytes)
            if reply:
                await self._speak_text(peer_id, reply)

    async def _audio_sender_loop(self, peer_id: int, ctx: Dict[str, Any]) -> None:
        frame_step = FRAME_MS / 1000.0
        next_tick = self.loop.time()
        try:
            while peer_id in self.active_calls:
                try:
                    frame = ctx["out_queue"].get_nowait()
                    ctx["ai_speaking"] = True
                    if self.ntg:
                        await self.ntg.send_external_frame(
                            peer_id,
                            ntgcalls.StreamDevice.MICROPHONE,
                            frame,
                            ntgcalls.FrameData(),
                        )
                except asyncio.QueueEmpty:
                    ctx["ai_speaking"] = False
                next_tick += frame_step
                delay = next_tick - self.loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_tick = self.loop.time()
        except asyncio.CancelledError:
            pass

    async def _cleanup_call(self, peer_id: int) -> None:
        ctx = self.active_calls.pop(peer_id, None)
        if not ctx:
            return
        tg_id = ctx.get("tg_call_id")
        if tg_id in self.call_by_tg_id:
            self.call_by_tg_id.pop(tg_id, None)
        task = ctx.get("sender_task")
        if task:
            task.cancel()
        if self.ntg:
            try:
                await self.ntg.stop(peer_id)
            except Exception:
                pass
        try:
            ctx["brain"].flush_transcript_to_vault()
        except Exception:
            pass


# ==============================================================================
# 6. LOCALHOST RPC + PROACTIVE CALL INVOCATION + INTERACTIVE CLI LOGIN
# ==============================================================================

_CALLER_MANAGER: Optional[TelegramP2PCallManager] = None


def _make_rpc_handler(manager: Optional[TelegramP2PCallManager]):
    class _CallerRpcHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _json(self, code: int, payload: Dict[str, Any]):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/status":
                st = get_caller_status()
                st["daemon_active"] = bool(manager and manager.client)
                self._json(200, st)
            else:
                self._json(404, {"error": "Not found"})

        def do_POST(self):
            if self.path == "/call":
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    data = {}
                if not manager or not manager.client:
                    self._json(503, {"success": False, "error": "Telegram caller daemon not connected"})
                    return
                opening = str(data.get("opening_prompt") or "")
                fut = asyncio.run_coroutine_threadsafe(
                    manager.initiate_outgoing_call(opening_prompt=opening),
                    manager.loop,
                )
                try:
                    res = fut.result(timeout=15)
                    self._json(200 if res.get("success") else 500, res)
                except Exception as exc:
                    self._json(500, {"success": False, "error": str(exc)})
            else:
                self._json(404, {"error": "Not found"})

    return _CallerRpcHandler


def request_call_via_rpc_or_direct(
    opening_prompt: str = "",
    urgent: bool = False,
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Requests a native 1-on-1 Telegram voice call via the running local RPC daemon.
    Respects `circadian_oracle.get_operational_mode` Sleep Shield unless `urgent=True`,
    and falls back gracefully when caller credentials/dependencies are absent.
    """
    if not urgent:
        try:
            import circadian_oracle
            op = circadian_oracle.get_operational_mode(vault_dir=vault_dir)
            if not op.get("allow_proactive_calls", True):
                return {"success": False, "suppressed": True, "reason": "SLEEP_SHIELD"}
        except Exception:
            pass

    try:
        req = urllib.request.Request(
            f"http://{RPC_HOST}:{RPC_PORT}/call",
            data=json.dumps({"opening_prompt": opening_prompt}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {
            "success": False,
            "fallback": "telegram_voice_note",
            "reason": str(exc),
        }


def start_telegram_caller_background():
    """
    Starts the localhost RPC control plane and, if Telethon+NTgCalls & session are ready,
    connects the single Telegram P2P Voice Caller daemon in a background thread.
    """
    def _worker():
        global _CALLER_MANAGER
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        mgr = TelegramP2PCallManager(loop)
        _CALLER_MANAGER = mgr

        ThreadingHTTPServer.allow_reuse_address = True
        try:
            rpc_srv = ThreadingHTTPServer((RPC_HOST, RPC_PORT), _make_rpc_handler(mgr))
            threading.Thread(target=rpc_srv.serve_forever, daemon=True, name="TelegramCallerRPC").start()
        except Exception as re_err:
            print(f"⚠ Telegram Caller RPC notice: {re_err}")

        st = get_caller_status()
        if st.get("status") != "connected":
            print(f"📞 Telegram Voice Caller standby ({st.get('status')}): run `python telegram_voice_caller.py login` once to link +20 account.")
            return

        async def _runner():
            ok = await mgr.start()
            if ok and mgr.client:
                print(f"📞 Native Telegram 1-on-1 Voice Caller ACTIVE on RPC :{RPC_PORT}!")
                await mgr.client.run_until_disconnected()

        try:
            loop.run_until_complete(_runner())
        except Exception as ce:
            print(f"⚠ Telegram Voice Caller runtime notice: {ce}")

    t = threading.Thread(target=_worker, daemon=True, name="TelegramVoiceCaller")
    t.start()
    return t


def _render_qr_halfblock(url: str) -> str:
    """
    Renders a compact, high-contrast black-on-white QR code using Unicode half-blocks
    (2 QR rows per terminal line) so it fits cleanly inside browser SSH windows.
    """
    if not QRCODE_AVAILABLE or qrcode is None:
        return f"Open QR generator for URL:\n{url}"
    qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    lines: List[str] = []
    # White paper background + black modules works reliably on dark terminals
    prefix = "\x1b[30;107m"
    suffix = "\x1b[0m"
    for r in range(0, rows, 2):
        row_chars = []
        top_row = matrix[r]
        bot_row = matrix[r + 1] if (r + 1) < rows else [False] * cols
        for t_cell, b_cell in zip(top_row, bot_row):
            if t_cell and b_cell:
                row_chars.append("█")
            elif t_cell and not b_cell:
                row_chars.append("▀")
            elif not t_cell and b_cell:
                row_chars.append("▄")
            else:
                row_chars.append(" ")
        lines.append(f"{prefix}{''.join(row_chars)}{suffix}")
    return "\n".join(lines)


def _prompt_with_saved(label: str, saved_value: Any = "") -> str:
    saved_str = str(saved_value or "").strip()
    suffix = f" [Enter to keep `{saved_str}`]" if saved_str else ""
    entered = input(f"{label}{suffix}: ").strip()
    return entered or saved_str


def interactive_login(mode: str = "qr") -> int:
    """
    Interactive CLI wizard saving Telegram API credentials outside git (`~/.aethelgard/credentials/`).
    Defaults to QR login (`Settings -> Devices -> Link Desktop Device`) so Telegram never flags OTP codes.
    Also supports explicit `phone_code_hash` OTP login cleanly without Telethon retry bugs.
    """
    if not TELETHON_AVAILABLE:
        print("ERROR: telethon is not installed. Run `pip install telethon ntgcalls qrcode`.")
        return 1

    import getpass

    existing = load_caller_config()
    print("=== Aethelgard Dedicated Telegram Caller Login (+20 / Personal MTProto) ===")
    api_id = int(_prompt_with_saved("Telegram api_id (from my.telegram.org)", existing.get("api_id")))
    api_hash = _prompt_with_saved("Telegram api_hash", existing.get("api_hash"))
    phone = _prompt_with_saved("Dedicated Phone Number (e.g. +2010...)", existing.get("phone"))

    cfg = save_caller_config(api_id=api_id, api_hash=api_hash, phone=phone)
    session_path = cfg["session_path"]

    async def _auth():
        client = TelegramClient(
            session_path,
            api_id,
            api_hash,
            device_model="Ubuntu Workstation",
            system_version="Linux 6.8",
            app_version="4.16.0",
        )
        await client.connect()
        try:
            if await client.is_user_authorized():
                me = await client.get_me()
                print(f"✅ Already signed in as {getattr(me, 'first_name', '')} ({getattr(me, 'phone', '')}) -> {session_path}")
                return

            use_qr = (mode == "qr") and QRCODE_AVAILABLE
            if mode == "ask":
                choice = input("Login method — [1] QR Code (Recommended, no OTP needed) or [2] OTP Code [1]: ").strip()
                use_qr = (choice != "2") and QRCODE_AVAILABLE

            if use_qr:
                print("\n📱 On your +20 Telegram phone app:")
                print("   Open Settings -> Devices -> Link Desktop Device, then scan this QR code:\n")
                qr_login = await client.qr_login()
                signed_in = False
                for attempt in range(4):
                    print(_render_qr_halfblock(qr_login.url))
                    print(f"\n⏳ Waiting up to 35s for scan (attempt {attempt + 1}/4)...\n")
                    try:
                        await qr_login.wait(timeout=35)
                        signed_in = True
                        break
                    except asyncio.TimeoutError:
                        if attempt < 3:
                            print("🔄 QR expired, refreshing...")
                            await qr_login.recreate()
                    except errors.SessionPasswordNeededError:
                        pwd = getpass.getpass("🔐 Two-Step Verification password required: ")
                        await client.sign_in(password=pwd)
                        signed_in = True
                        break
                if not signed_in:
                    print("⚠ QR scan timed out. Switching to OTP code login...")
                    use_qr = False

            if not use_qr:
                sent = await client.send_code_request(phone)
                for attempt in range(3):
                    code = input("Please enter the code you received (do NOT paste it anywhere inside Telegram): ").strip()
                    try:
                        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
                        break
                    except errors.SessionPasswordNeededError:
                        pwd = getpass.getpass("🔐 Two-Step Verification password: ")
                        await client.sign_in(password=pwd)
                        break
                    except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as exc:
                        print(f"⚠ {exc}. Requesting a fresh code...")
                        if attempt >= 2:
                            raise
                        sent = await client.send_code_request(phone)

            me = await client.get_me()
            print(f"✅ Signed in as {getattr(me, 'first_name', '')} ({getattr(me, 'phone', '')}) -> {session_path}")
        finally:
            await client.disconnect()

    asyncio.run(_auth())
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Native Telegram 1-on-1 Voice Caller")
    parser.add_argument("action", nargs="?", choices=["login", "status", "call"], default="status")
    parser.add_argument("--qr", action="store_true", help="Force QR-code device linking login (recommended)")
    parser.add_argument("--otp", action="store_true", help="Force OTP phone-code login instead of QR")
    parser.add_argument("--call", metavar="PROMPT", help="Trigger an outgoing 1-on-1 voice call to Omar")
    parser.add_argument("--urgent", action="store_true", help="Override Circadian Sleep Shield for this call")
    args = parser.parse_args()

    if args.action == "login":
        mode = "otp" if args.otp else "qr"
        return interactive_login(mode=mode)
    if args.call is not None or args.action == "call":
        res = request_call_via_rpc_or_direct(opening_prompt=args.call or "", urgent=args.urgent)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("success") else 1

    print(json.dumps(get_caller_status(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
