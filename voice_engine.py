import os
import re
import shutil
import threading
import time
import uuid
import json
import asyncio
import http.client
import subprocess
import tempfile
import urllib.parse
import urllib.request
import edge_tts

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")

VOICE_ENGLISH = "en-GB-RyanNeural"  # Paul Bettany / Classic British Jarvis
VOICE_ARABIC = "ar-SY-LaithNeural"    # Authentic Syrian Male
JARVIS_PITCH = "-4Hz"                 # Deeper, calm resonance
JARVIS_RATE = "-2%"                   # Measured cadence

# Circuit breaker so if Bing ReadAloud returns 403 on a cloud IP, subsequent
# live-call sentences skip the doomed handshake and synthesize immediately.
_EDGE_TTS_BLOCKED_UNTIL = 0.0
_EDGE_TTS_COOLDOWN_SEC = 1800.0
_TTS_TLS_LOCAL = threading.local()


def is_arabic(text: str) -> bool:
    """Checks if text contains substantial Arabic characters."""
    arabic_chars = re.findall(r"[\u0600-\u06FF]", text)
    return len(arabic_chars) > len(text) * 0.2


def _clean_tts_text(text: str, default: str = "") -> str:
    clean = re.sub(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]", r"\1", text or "")
    clean = re.sub(r"[*_`#\[\]()~>|]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or default


def _split_tts_http_chunks(text: str, max_chars: int = 175) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        cut = remaining.rfind(" ", 0, max_chars)
        if cut <= 20:
            cut = max_chars
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [c for c in chunks if c]


def _google_tts_http_get_sync(path_and_query: str) -> bytes:
    """Fetches Google TTS audio over a thread-local keep-alive TLS socket for low latency."""
    conn = getattr(_TTS_TLS_LOCAL, "conn", None)
    headers = {"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"}
    for attempt in range(2):
        try:
            if conn is None:
                conn = http.client.HTTPSConnection("translate.googleapis.com", timeout=8)
                _TTS_TLS_LOCAL.conn = conn
            conn.request("GET", path_and_query, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            if resp.status == 200 and data:
                return data
            if attempt == 0:
                try:
                    conn.close()
                except Exception:
                    pass
                _TTS_TLS_LOCAL.conn = None
                conn = None
        except Exception:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            _TTS_TLS_LOCAL.conn = None
            conn = None

    url = f"https://translate.googleapis.com{path_and_query}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def _synthesize_google_tts_mp3_sync(text: str, is_ar: bool) -> bytes:
    """Resilient HTTPS fallback via Google TTS (works reliably on GCP IPs when Bing returns 403)."""
    lang = "ar" if is_ar else "en-GB"
    audio_parts: list[bytes] = []
    for chunk in _split_tts_http_chunks(text, max_chars=175):
        q = urllib.parse.quote(chunk)
        data = _google_tts_http_get_sync(f"/translate_tts?ie=UTF-8&client=gtx&tl={lang}&q={q}")
        if data:
            audio_parts.append(data)
    return b"".join(audio_parts)


def _synthesize_espeak_wav_sync(text: str, is_ar: bool) -> bytes:
    """Last-resort offline speech synthesis via espeak-ng/espeak if network TTS fails."""
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary:
        return b""
    voice_arg = "ar" if is_ar else "en-gb"
    proc = subprocess.run(
        [binary, "-v", voice_arg, "-s", "158", "-p", "42", "--stdout", text],
        capture_output=True,
        check=False,
        timeout=15,
    )
    return proc.stdout if proc.returncode == 0 else b""


async def _synthesize_edge_mp3(clean_text: str, voice: str, is_ar: bool) -> bytes:
    global _EDGE_TTS_BLOCKED_UNTIL
    if time.monotonic() < _EDGE_TTS_BLOCKED_UNTIL:
        raise RuntimeError("edge-tts temporarily circuit-broken after 403")
    pitch = "-1Hz" if is_ar else JARVIS_PITCH
    rate = "+0%" if is_ar else JARVIS_RATE
    try:
        communicate = edge_tts.Communicate(clean_text, voice, pitch=pitch, rate=rate)
        mp3_chunks: list[bytes] = []

        async def _collect_edge_audio():
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    mp3_chunks.append(chunk["data"])

        await asyncio.wait_for(_collect_edge_audio(), timeout=4.0)
        payload = b"".join(mp3_chunks)
        if not payload:
            raise RuntimeError("edge-tts returned empty audio stream")
        return payload
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "Invalid response status" in msg:
            _EDGE_TTS_BLOCKED_UNTIL = time.monotonic() + _EDGE_TTS_COOLDOWN_SEC
        raise


def _ffmpeg_transcode(payload: bytes, input_fmt: str, output_fmt: str, sample_rate: int, channels: int) -> bytes:
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        input_fmt,
        "-i",
        "pipe:0",
    ]
    if output_fmt == "s16le":
        cmd.extend(["-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(channels), "-ar", str(sample_rate)])
    elif output_fmt == "ogg":
        cmd.extend(["-f", "ogg", "-c:a", "libopus", "-b:a", "48k", "-ac", str(channels), "-ar", str(sample_rate)])
    else:
        cmd.extend(["-f", output_fmt])
    cmd.append("pipe:1")

    proc = subprocess.run(cmd, input=payload, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or "ffmpeg transcode failed")
    return proc.stdout


async def generate_voice_file(text: str, output_path: str, voice: str = "auto") -> str:
    """Generates an audio voice note file using edge-tts with automatic Google/offline fallback."""
    global _EDGE_TTS_BLOCKED_UNTIL
    is_ar = is_arabic(text)
    if voice == "auto":
        voice = VOICE_ARABIC if is_ar else VOICE_ENGLISH

    clean_text = _clean_tts_text(text, default="Task completed, sir.")

    try:
        pitch = "-1Hz" if is_ar else JARVIS_PITCH
        rate = "+0%" if is_ar else JARVIS_RATE
        if time.monotonic() < _EDGE_TTS_BLOCKED_UNTIL:
            raise RuntimeError("edge-tts temporarily circuit-broken")
        communicate = edge_tts.Communicate(clean_text, voice, pitch=pitch, rate=rate)
        await communicate.save(output_path)
        return output_path
    except Exception as edge_err:
        if "403" in str(edge_err) or "Invalid response status" in str(edge_err):
            _EDGE_TTS_BLOCKED_UNTIL = time.monotonic() + _EDGE_TTS_COOLDOWN_SEC
        ext = os.path.splitext(output_path)[1].lower().lstrip(".") or "ogg"
        out_fmt = "mp3" if ext == "mp3" else "ogg"
        mp3_bytes = await asyncio.to_thread(_synthesize_google_tts_mp3_sync, clean_text, is_ar)
        if mp3_bytes:
            if out_fmt == "mp3":
                with open(output_path, "wb") as f:
                    f.write(mp3_bytes)
            else:
                ogg_bytes = await asyncio.to_thread(_ffmpeg_transcode, mp3_bytes, "mp3", "ogg", 48000, 1)
                with open(output_path, "wb") as f:
                    f.write(ogg_bytes)
            return output_path
        raise edge_err

def send_telegram_voice_sync(text: str, caption: str = None, chat_id: str = TELEGRAM_USER_ID, token: str = TELEGRAM_BOT_TOKEN) -> bool:
    """Synchronously generates and uploads a voice note directly to Telegram."""
    temp_dir = tempfile.gettempdir()
    ogg_path = os.path.join(temp_dir, f"voice_{uuid.uuid4().hex[:8]}.ogg")
    try:
        asyncio.run(generate_voice_file(text, ogg_path))
        
        boundary = uuid.uuid4().hex
        c_type = f"multipart/form-data; boundary={boundary}"

        parts = []
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode('utf-8'))
        if caption:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode('utf-8'))
        
        with open(ogg_path, "rb") as f:
            voice_bytes = f.read()
        
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; filename="voice.ogg"\r\nContent-Type: audio/ogg\r\n\r\n'.encode('utf-8'))
        parts.append(voice_bytes)
        parts.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))

        data = b"".join(parts)
        url = f"https://api.telegram.org/bot{token}/sendVoice"
        req = urllib.request.Request(url, data=data, headers={"Content-Type": c_type})

        with urllib.request.urlopen(req, timeout=20) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res.get("ok", False)
    except Exception as e:
        print(f"⚠ Voice delivery notice: {e}")
        return False
    finally:
        if os.path.exists(ogg_path):
            try:
                os.remove(ogg_path)
            except Exception:
                pass


def split_speakable_sentences(text: str, max_chars: int = 220) -> list[str]:
    """
    Splits clean assistant prose into low-latency speakable clauses for progressive TTS.
    Respects English and Arabic punctuation while preserving short decimal/abbreviation flows.
    """
    clean = re.sub(r"[*_`#\[\]()~>|]", "", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return []

    raw_parts = re.split(r"(?<=[.!?\u061F\u06D4;:])\s+|\n+", clean)
    clauses: list[str] = []
    for part in raw_parts:
        chunk = part.strip()
        if not chunk:
            continue
        while len(chunk) > max_chars:
            cut = chunk.rfind(" ", 0, max_chars)
            if cut <= 20:
                cut = max_chars
            clauses.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            clauses.append(chunk)
    return clauses


async def synthesize_pcm_bytes(
    text: str,
    voice: str = "auto",
    sample_rate: int = 48000,
    channels: int = 1,
) -> bytes:
    """
    Synthesizes speech via 3-tier fallback (edge-tts -> Google TTS HTTPS -> espeak-ng)
    and decodes audio into raw s16le PCM bytes using ffmpeg.
    """
    is_ar = is_arabic(text)
    if voice == "auto":
        voice = VOICE_ARABIC if is_ar else VOICE_ENGLISH

    clean_text = _clean_tts_text(text)
    if not clean_text:
        return b""

    payload = b""
    input_fmt = "mp3"
    edge_err: Exception | None = None

    try:
        payload = await _synthesize_edge_mp3(clean_text, voice, is_ar)
    except Exception as exc:
        edge_err = exc

    if not payload:
        try:
            payload = await asyncio.to_thread(_synthesize_google_tts_mp3_sync, clean_text, is_ar)
            input_fmt = "mp3"
        except Exception:
            payload = b""

    if not payload:
        try:
            payload = await asyncio.to_thread(_synthesize_espeak_wav_sync, clean_text, is_ar)
            input_fmt = "wav"
        except Exception:
            payload = b""

    if not payload:
        if edge_err is not None:
            raise edge_err
        return b""

    return await asyncio.to_thread(_ffmpeg_transcode, payload, input_fmt, "s16le", sample_rate, channels)


async def synthesize_pcm_frames(
    text: str,
    voice: str = "auto",
    sample_rate: int = 48000,
    channels: int = 1,
    frame_ms: int = 20,
):
    """
    Async generator yielding fixed-duration s16le PCM frames (default 20ms = 1920 bytes @ 48kHz mono)
    sentence-by-sentence with next-sentence lookahead prefetch for seamless live voice playback.
    """
    frame_bytes = (sample_rate * 2 * channels * frame_ms) // 1000
    sentences = split_speakable_sentences(text)
    if not sentences:
        return

    next_task = asyncio.create_task(
        synthesize_pcm_bytes(sentences[0], voice=voice, sample_rate=sample_rate, channels=channels)
    )
    for idx in range(len(sentences)):
        pcm = await next_task
        if idx + 1 < len(sentences):
            next_task = asyncio.create_task(
                synthesize_pcm_bytes(sentences[idx + 1], voice=voice, sample_rate=sample_rate, channels=channels)
            )
        if not pcm:
            continue
        for offset in range(0, len(pcm), frame_bytes):
            frame = pcm[offset : offset + frame_bytes]
            if len(frame) < frame_bytes:
                frame = frame + b"\x00" * (frame_bytes - len(frame))
            yield frame

