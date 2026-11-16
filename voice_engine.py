import os
import io
import re
import sys
import wave
import shutil
import contextvars
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
from pathlib import Path
import edge_tts

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")

VOICE_ENGLISH = "en-GB-RyanNeural"  # Paul Bettany / Classic British Jarvis
VOICE_ARABIC = "ar-SY-LaithNeural"    # Authentic Syrian Male
JARVIS_PITCH = "-4Hz"                 # Deeper, calm resonance
JARVIS_RATE = "-2%"                   # Measured cadence

PIPER_VOICE_ENGLISH = "en_GB-alan-medium"  # Offline/cloud-proof calm British Male neural voice
PIPER_VOICE_ARABIC = "ar_JO-kareem-medium"  # Offline/cloud-proof Arabic Male neural voice
_PIPER_VOICE_FILES = {
    "en_GB-alan-medium": "en/en_GB/alan/medium/en_GB-alan-medium.onnx",
    "ar_JO-kareem-medium": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx",
}
_PIPER_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"

# Circuit breaker so if Bing ReadAloud returns 403 transiently, follow-up
# sentences skip the doomed handshake briefly and recover automatically.
_EDGE_TTS_BLOCKED_UNTIL = 0.0
_EDGE_TTS_COOLDOWN_SEC = 120.0
_TTS_TLS_LOCAL = threading.local()
_PIPER_LOCK = threading.Lock()
_FFMPEG_PROC_LOCK = threading.Lock()
_LOW_CPU_TTS_CTX: contextvars.ContextVar[bool] = contextvars.ContextVar("aethelgard_low_cpu_tts", default=False)
_COALESCE_CHARS_CTX: contextvars.ContextVar[int] = contextvars.ContextVar("aethelgard_tts_coalesce_chars", default=0)
_PIPER_VOICE_CACHE: dict[str, object] = {}
_PIPER_DOWNLOAD_LOCK = threading.Lock()
_PIPER_DOWNLOADING: set[str] = set()


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


def _get_voices_dir() -> Path:
    explicit = os.environ.get("AETHELGARD_VOICES_DIR", "").strip()
    if explicit:
        path = Path(os.path.expanduser(explicit))
    else:
        base = os.environ.get("AETHELGARD_HOME", "").strip()
        root = Path(os.path.expanduser(base)) if base else (Path.home() / ".aethelgard")
        path = root / "voices"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_piper_voice_files(voice_id: str = PIPER_VOICE_ENGLISH, timeout: float = 45.0) -> tuple[Path, Path] | None:
    """Downloads a Piper ONNX voice + config atomically if missing; returns (onnx_path, json_path)."""
    rel = _PIPER_VOICE_FILES.get(voice_id)
    if not rel:
        return None
    voices_dir = _get_voices_dir()
    onnx_path = voices_dir / f"{voice_id}.onnx"
    cfg_path = voices_dir / f"{voice_id}.onnx.json"
    if onnx_path.is_file() and onnx_path.stat().st_size > 100_000 and cfg_path.is_file() and cfg_path.stat().st_size > 100:
        return onnx_path, cfg_path

    for suffix, target_path in (("", onnx_path), (".json", cfg_path)):
        url = f"{_PIPER_HF_BASE}{rel}{suffix}"
        part_path = voices_dir / f"{target_path.name}.{uuid.uuid4().hex[:6]}.part"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Aethelgard-VoiceEngine/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(part_path, "wb") as out_f:
                shutil.copyfileobj(resp, out_f)
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            os.replace(part_path, target_path)
        except Exception as exc:
            part_path.unlink(missing_ok=True)
            print(f"[Voice] Piper voice fetch notice ({voice_id}{suffix}): {type(exc).__name__}: {exc}", flush=True)
            return None
    return onnx_path, cfg_path


def _kickoff_piper_prefetch_async(voice_id: str) -> None:
    """Fetches a missing Piper voice model in a non-blocking daemon thread so live turns never stall."""
    if os.environ.get("AETHELGARD_NO_AUTO_VOICE_DOWNLOAD") == "1":
        return
    with _PIPER_DOWNLOAD_LOCK:
        if voice_id in _PIPER_DOWNLOADING:
            return
        _PIPER_DOWNLOADING.add(voice_id)

    def _bg():
        try:
            ensure_piper_voice_files(voice_id)
        finally:
            with _PIPER_DOWNLOAD_LOCK:
                _PIPER_DOWNLOADING.discard(voice_id)

    threading.Thread(target=_bg, daemon=True, name=f"PiperFetch-{voice_id}").start()


def _synthesize_piper_wav_sync(text: str, is_ar: bool, allow_blocking_download: bool = False) -> bytes:
    """Synthesizes calm British/Arabic Male speech locally via piper-tts if cached on disk."""
    try:
        from piper.voice import PiperVoice
    except ImportError:
        return b""

    voice_id = PIPER_VOICE_ARABIC if is_ar else PIPER_VOICE_ENGLISH
    voices_dir = _get_voices_dir()
    onnx_path = voices_dir / f"{voice_id}.onnx"
    cfg_path = voices_dir / f"{voice_id}.onnx.json"
    if not (onnx_path.is_file() and cfg_path.is_file()):
        if allow_blocking_download:
            res = ensure_piper_voice_files(voice_id)
            if not res:
                return b""
            onnx_path, cfg_path = res
        else:
            _kickoff_piper_prefetch_async(voice_id)
            return b""

    with _PIPER_LOCK:
        voice_obj = _PIPER_VOICE_CACHE.get(voice_id)
        if voice_obj is None:
            # Keep at most 1 ONNX voice in RAM for constrained 1GB e2-micro VMs.
            _PIPER_VOICE_CACHE.clear()
            voice_obj = PiperVoice.load(onnx_path, config_path=cfg_path, use_cuda=False)
            _PIPER_VOICE_CACHE[voice_id] = voice_obj

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            voice_obj.synthesize_wav(text, wf)
        return buf.getvalue()


async def _synthesize_edge_mp3(clean_text: str, voice: str, is_ar: bool) -> bytes:
    global _EDGE_TTS_BLOCKED_UNTIL
    if time.monotonic() < _EDGE_TTS_BLOCKED_UNTIL:
        raise RuntimeError("edge-tts temporarily circuit-broken after error")
    low_cpu = bool(_LOW_CPU_TTS_CTX.get() or os.environ.get("AETHELGARD_LOW_CPU_TTS", "0") == "1")
    timeout_sec = 4.5 if low_cpu else 8.0
    pitch = "-1Hz" if is_ar else JARVIS_PITCH
    rate = "+0%" if is_ar else JARVIS_RATE
    try:
        communicate = edge_tts.Communicate(clean_text, voice, pitch=pitch, rate=rate)
        mp3_chunks: list[bytes] = []

        async def _collect_edge_audio():
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    mp3_chunks.append(chunk["data"])

        await asyncio.wait_for(_collect_edge_audio(), timeout=timeout_sec)
        payload = b"".join(mp3_chunks)
        if not payload:
            raise RuntimeError("edge-tts returned empty audio stream")
        return payload
    except Exception as exc:
        msg = str(exc)
        print(f"[Voice] Edge-TTS error ({voice}): {type(exc).__name__}: {msg[:160]}", flush=True)
        if "403" in msg or "Invalid response status" in msg:
            _EDGE_TTS_BLOCKED_UNTIL = time.monotonic() + _EDGE_TTS_COOLDOWN_SEC
            if not low_cpu and os.environ.get("AETHELGARD_PRELOAD_PIPER", "0") == "1":
                _kickoff_piper_prefetch_async(PIPER_VOICE_ARABIC if is_ar else PIPER_VOICE_ENGLISH)
        elif low_cpu and isinstance(exc, (asyncio.TimeoutError, TimeoutError, OSError)):
            # Briefly skip slow/hanging Bing socket during live calls so 0.27s cloud fallback kicks in immediately.
            _EDGE_TTS_BLOCKED_UNTIL = max(_EDGE_TTS_BLOCKED_UNTIL, time.monotonic() + 60.0)
        raise


def _scale_pcm16_volume(pcm: bytes, audio_filter: str = "") -> bytes:
    """Scales s16le PCM amplitude using C audioop (Python 3.11) with pure-Python array fallback (3.13+)."""
    if not pcm or not audio_filter:
        return pcm
    match = re.fullmatch(r"volume=([0-9.]+)", audio_filter.strip())
    if not match:
        return pcm
    vol = float(match.group(1))
    if not (0.0 <= vol <= 2.0) or abs(vol - 1.0) <= 1e-3:
        return pcm
    try:
        import audioop  # type: ignore
        return audioop.mul(pcm, 2, vol)
    except Exception:
        import array
        usable = len(pcm) - (len(pcm) % 2)
        if usable <= 0:
            return pcm
        samples = array.array("h")
        samples.frombytes(pcm[:usable])
        if sys.byteorder != "little":
            samples.byteswap()
        scaled = array.array("h", (max(-32768, min(32767, int(s * vol))) for s in samples))
        if sys.byteorder != "little":
            scaled.byteswap()
        return scaled.tobytes()


def _fast_wav_to_s16le_inprocess(
    payload: bytes,
    sample_rate: int,
    channels: int,
    audio_filter: str = "",
) -> bytes | None:
    """
    Zero-subprocess WAV -> s16le resampler & gain scaler using C stdlib audioop (Python 3.11)
    to eliminate per-sentence ffmpeg CPU spikes on 1-vCPU e2-micro VMs during live calls.
    """
    if not (payload and payload[:4] == b"RIFF" and len(payload) > 44):
        return None
    if audio_filter and not re.fullmatch(r"volume=[0-9.]+", audio_filter.strip()):
        return None
    try:
        import audioop
        import wave
    except Exception:
        return None

    try:
        with wave.open(io.BytesIO(payload), "rb") as wf:
            src_ch = wf.getnchannels()
            src_sw = wf.getsampwidth()
            src_rate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
        if not pcm:
            return b""
        if src_sw != 2:
            pcm = audioop.lin2lin(pcm, src_sw, 2)
        if src_ch == 2 and channels == 1:
            pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        elif src_ch == 1 and channels == 2:
            pcm = audioop.tostereo(pcm, 2, 1.0, 1.0)
        if src_rate != sample_rate:
            pcm, _ = audioop.ratecv(pcm, 2, channels, src_rate, sample_rate, None)
        return _scale_pcm16_volume(pcm, audio_filter)
    except Exception:
        return None


def _fast_mp3_to_s16le_inprocess(
    payload: bytes,
    sample_rate: int,
    channels: int,
    audio_filter: str = "",
) -> bytes | None:
    """
    Zero-subprocess MP3 -> s16le decoder using optional C extension `miniaudio` (dr_mp3)
    + C/array gain scaling to eliminate ~3s ffmpeg cold spawns on 1-vCPU VMs.
    """
    if os.environ.get("AETHELGARD_DISABLE_MINIAUDIO") == "1":
        return None
    if not payload or len(payload) < 128:
        return None
    is_mp3 = payload.startswith(b"ID3") or (len(payload) >= 2 and payload[0] == 0xFF and (payload[1] & 0xE0) == 0xE0)
    if not is_mp3:
        return None
    if audio_filter and not re.fullmatch(r"volume=[0-9.]+", audio_filter.strip()):
        return None
    try:
        import miniaudio  # type: ignore
    except Exception:
        return None
    try:
        decoded = miniaudio.decode(
            payload,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=int(channels),
            sample_rate=int(sample_rate),
        )
        samples = decoded.samples
        if hasattr(samples, "tobytes"):
            if sys.byteorder != "little":
                samples.byteswap()
            pcm = samples.tobytes()
        else:
            pcm = bytes(samples)
        if not pcm:
            return b""
        return _scale_pcm16_volume(pcm, audio_filter)
    except Exception:
        return None


def _ffmpeg_transcode(
    payload: bytes,
    input_fmt: str,
    output_fmt: str,
    sample_rate: int,
    channels: int,
    audio_filter: str = "",
) -> bytes:
    if input_fmt == "wav" and output_fmt == "s16le":
        fast = _fast_wav_to_s16le_inprocess(payload, sample_rate, channels, audio_filter=audio_filter)
        if fast is not None:
            return fast
    if input_fmt == "mp3" and output_fmt == "s16le":
        fast_mp3 = _fast_mp3_to_s16le_inprocess(payload, sample_rate, channels, audio_filter=audio_filter)
        if fast_mp3 is not None:
            return fast_mp3

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "1",
        "-f",
        input_fmt,
        "-i",
        "pipe:0",
    ]
    post_vol: float | None = None
    if (
        input_fmt == "mp3"
        and output_fmt == "s16le"
        and audio_filter
        and re.fullmatch(r"volume=[0-9.]+", audio_filter.strip())
    ):
        post_vol = float(audio_filter.strip().split("=", 1)[1])
    elif audio_filter:
        cmd.extend(["-af", audio_filter])
    if output_fmt == "s16le":
        cmd.extend(["-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(channels), "-ar", str(sample_rate)])
    elif output_fmt == "ogg":
        cmd.extend(["-f", "ogg", "-c:a", "libopus", "-b:a", "48k", "-ac", str(channels), "-ar", str(sample_rate)])
    elif output_fmt == "mp3":
        cmd.extend(["-f", "mp3", "-c:a", "libmp3lame", "-q:a", "4", "-ac", str(channels), "-ar", str(sample_rate)])
    else:
        cmd.extend(["-f", output_fmt])
    cmd.append("pipe:1")

    with _FFMPEG_PROC_LOCK:
        proc = subprocess.run(cmd, input=payload, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or "ffmpeg transcode failed")
    out = proc.stdout
    if post_vol is not None and out:
        out = _scale_pcm16_volume(out, audio_filter)
    return out


def _fallback_audio_filter(tier: str, is_ar: bool) -> str:
    """Normalizes level and ensures male baritone register if forced onto Google Translate TTS."""
    if tier == "edge":
        return "volume=0.88"
    if tier == "piper":
        return "volume=0.80"
    if tier == "google_tts":
        # Shift bright/loud female Google Translate TTS down into a calm male conversational register.
        factor = "0.87" if is_ar else "0.85"
        tempo = "1.1494" if is_ar else "1.17647"
        return f"aresample=48000,asetrate=48000*{factor},atempo={tempo},aresample=48000,volume=0.62"
    return "volume=0.72"


async def _synthesize_speech_payload_async(
    clean_text: str,
    voice: str,
    is_ar: bool,
    prefer_cloud_fallback: bool = False,
) -> tuple[bytes, str, str, Exception | None]:
    """4-tier male JARVIS synthesis: Edge-TTS -> (Google/Piper based on CPU mode) -> espeak-ng."""
    edge_err: Exception | None = None
    low_cpu = bool(
        prefer_cloud_fallback
        or _LOW_CPU_TTS_CTX.get()
        or os.environ.get("AETHELGARD_LOW_CPU_TTS", "0") == "1"
    )
    try:
        payload = await _synthesize_edge_mp3(clean_text, voice, is_ar)
        if payload:
            return payload, "mp3", _fallback_audio_filter("edge", is_ar), None
    except Exception as exc:
        edge_err = exc

    if low_cpu:
        try:
            gtts_mp3 = await asyncio.to_thread(_synthesize_google_tts_mp3_sync, clean_text, is_ar)
            if gtts_mp3:
                return gtts_mp3, "mp3", _fallback_audio_filter("google_tts", is_ar), edge_err
        except Exception:
            pass
        try:
            piper_wav = await asyncio.to_thread(_synthesize_piper_wav_sync, clean_text, is_ar)
            if piper_wav:
                return piper_wav, "wav", _fallback_audio_filter("piper", is_ar), edge_err
        except Exception:
            pass
    else:
        try:
            piper_wav = await asyncio.to_thread(_synthesize_piper_wav_sync, clean_text, is_ar)
            if piper_wav:
                return piper_wav, "wav", _fallback_audio_filter("piper", is_ar), edge_err
        except Exception:
            pass

        try:
            gtts_mp3 = await asyncio.to_thread(_synthesize_google_tts_mp3_sync, clean_text, is_ar)
            if gtts_mp3:
                return gtts_mp3, "mp3", _fallback_audio_filter("google_tts", is_ar), edge_err
        except Exception:
            pass

    try:
        espeak_wav = await asyncio.to_thread(_synthesize_espeak_wav_sync, clean_text, is_ar)
        if espeak_wav:
            return espeak_wav, "wav", _fallback_audio_filter("espeak", is_ar), edge_err
    except Exception:
        pass

    return b"", "", "", edge_err


async def generate_voice_file(text: str, output_path: str, voice: str = "auto") -> str:
    """Generates an audio voice note file using the 4-tier British/Arabic male JARVIS stack."""
    is_ar = is_arabic(text)
    if voice == "auto":
        voice = VOICE_ARABIC if is_ar else VOICE_ENGLISH

    clean_text = _clean_tts_text(text, default="Task completed, sir.")
    payload, input_fmt, audio_filter, err = await _synthesize_speech_payload_async(clean_text, voice, is_ar)
    if not payload:
        raise err or RuntimeError("All TTS backends failed")

    ext = os.path.splitext(output_path)[1].lower().lstrip(".") or "ogg"
    out_fmt = "mp3" if ext == "mp3" else "ogg"
    transcoded = await asyncio.to_thread(
        _ffmpeg_transcode, payload, input_fmt, out_fmt, 48000, 1, audio_filter=audio_filter
    )
    with open(output_path, "wb") as f:
        f.write(transcoded)
    return output_path

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
    prefer_cloud_fallback: bool = False,
) -> bytes:
    """
    Synthesizes speech via 4-tier male JARVIS fallback (edge-tts -> piper-tts ->
    pitch/volume-shaped Google TTS -> espeak-ng) and decodes audio into s16le PCM.
    """
    is_ar = is_arabic(text)
    if voice == "auto":
        voice = VOICE_ARABIC if is_ar else VOICE_ENGLISH

    clean_text = _clean_tts_text(text)
    if not clean_text:
        return b""

    low_cpu = bool(prefer_cloud_fallback or _LOW_CPU_TTS_CTX.get())
    tok = _LOW_CPU_TTS_CTX.set(low_cpu)
    try:
        payload, input_fmt, audio_filter, edge_err = await _synthesize_speech_payload_async(
            clean_text, voice, is_ar, prefer_cloud_fallback=low_cpu
        )
    finally:
        _LOW_CPU_TTS_CTX.reset(tok)
    if not payload:
        if edge_err is not None:
            raise edge_err
        return b""

    return await asyncio.to_thread(
        _ffmpeg_transcode, payload, input_fmt, "s16le", sample_rate, channels, audio_filter=audio_filter
    )


def _coalesce_speakable_clauses(clauses: list[str], max_chars: int) -> list[str]:
    """Merges adjacent short sentences into batches up to max_chars to avoid per-sentence TTS/ffmpeg overhead."""
    if max_chars <= 0 or len(clauses) <= 1:
        return clauses
    merged: list[str] = []
    cur = ""
    for clause in clauses:
        part = clause.strip()
        if not part:
            continue
        if not cur:
            cur = part
        elif len(cur) + 1 + len(part) <= max_chars:
            cur = f"{cur} {part}"
        else:
            merged.append(cur)
            cur = part
    if cur:
        merged.append(cur)
    return merged


async def synthesize_pcm_frames(
    text: str,
    voice: str = "auto",
    sample_rate: int = 48000,
    channels: int = 1,
    frame_ms: int = 20,
    prefer_cloud_fallback: bool = False,
    coalesce_chars: int = 0,
):
    """
    Async generator yielding fixed-duration s16le PCM frames (default 20ms = 1920 bytes @ 48kHz mono)
    sentence-by-sentence with optional low-CPU batch coalescing and sequential VM-safe execution.
    """
    frame_bytes = (sample_rate * 2 * channels * frame_ms) // 1000
    sentences = split_speakable_sentences(text)
    if not sentences:
        return
    eff_coalesce = int(coalesce_chars or _COALESCE_CHARS_CTX.get() or 0)
    if eff_coalesce > 0:
        sentences = _coalesce_speakable_clauses(sentences, eff_coalesce)
    low_cpu = bool(
        prefer_cloud_fallback
        or _LOW_CPU_TTS_CTX.get()
        or os.environ.get("AETHELGARD_LOW_CPU_TTS", "0") == "1"
    )
    tok = _LOW_CPU_TTS_CTX.set(low_cpu)
    try:
        if low_cpu or len(sentences) == 1:
            # Avoid overlapping concurrent ffmpeg/network spawns on 0.25-vCPU e2-micro VMs.
            for sent in sentences:
                pcm = await synthesize_pcm_bytes(sent, voice=voice, sample_rate=sample_rate, channels=channels)
                if not pcm:
                    continue
                for offset in range(0, len(pcm), frame_bytes):
                    frame = pcm[offset : offset + frame_bytes]
                    if len(frame) < frame_bytes:
                        frame = frame + b"\x00" * (frame_bytes - len(frame))
                    yield frame
        else:
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
    finally:
        _LOW_CPU_TTS_CTX.reset(tok)


# ---------------------------------------------------------------------------
# Pre-Cached JARVIS PCM Earcons (Zero-Latency Tool-Call Feedback)
# ---------------------------------------------------------------------------

EARCON_PHRASES: dict[str, tuple[str, str]] = {
    "checking": ("Checking now, sir.", "جاري التحقق الآن."),
    "drafting": ("Preparing that for you.", "جاري التحضير الآن."),
    "mission": ("Mission acknowledged.", "تم استلام المهمة."),
    "portal": ("Opening portal now.", "جاري فتح البوابة الآن."),
    "searching": ("Searching now.", "جاري البحث الآن."),
}

_EARCON_PCM_CACHE: dict[tuple[str, bool, int, int], list[bytes]] = {}
_EARCON_CACHE_LOCK = threading.Lock()


def _slice_pcm_frames(pcm: bytes, sample_rate: int = 48000, channels: int = 1, frame_ms: int = 10) -> list[bytes]:
    if not pcm:
        return []
    frame_bytes = int(sample_rate * channels * 2 * (frame_ms / 1000.0))
    if frame_bytes <= 0:
        return []
    frames: list[bytes] = []
    for offset in range(0, len(pcm), frame_bytes):
        frame = pcm[offset : offset + frame_bytes]
        if len(frame) < frame_bytes:
            frame = frame + b"\x00" * (frame_bytes - len(frame))
        frames.append(frame)
    return frames


def get_cached_earcon_frames(
    kind: str,
    is_ar: bool = False,
    sample_rate: int = 48000,
    channels: int = 1,
    frame_ms: int = 10,
) -> list[bytes]:
    """Return pre-cached PCM frames for an earcon kind (10ms default for NTgCalls), or [] if not cached."""
    key = (str(kind or "checking").strip().lower(), bool(is_ar), int(sample_rate), int(channels), int(frame_ms))
    with _EARCON_CACHE_LOCK:
        frames = _EARCON_PCM_CACHE.get(key)
        if not frames and is_ar:
            frames = _EARCON_PCM_CACHE.get((key[0], False, key[2], key[3], key[4]))
        return list(frames) if frames else []


async def preload_jarvis_earcons_async(
    sample_rate: int = 48000,
    channels: int = 1,
    frame_ms: int = 10,
    kinds: list[str] | None = None,
    include_arabic: bool = True,
) -> int:
    """Pre-synthesize British male (and optional Arabic) earcon PCM frames into RAM cache."""
    target_kinds = kinds or list(EARCON_PHRASES.keys())
    lang_slots = ((0, False), (1, True)) if include_arabic else ((0, False),)
    loaded = 0
    for kind in target_kinds:
        pair = EARCON_PHRASES.get(kind)
        if not pair:
            continue
        for idx, is_ar in lang_slots:
            key = (kind, is_ar, int(sample_rate), int(channels), int(frame_ms))
            with _EARCON_CACHE_LOCK:
                if _EARCON_PCM_CACHE.get(key):
                    loaded += 1
                    continue
            text = pair[idx]
            voice = VOICE_ARABIC if is_ar else VOICE_ENGLISH
            tok = _LOW_CPU_TTS_CTX.set(True)
            try:
                pcm = await synthesize_pcm_bytes(
                    text,
                    voice=voice,
                    sample_rate=sample_rate,
                    channels=channels,
                )
                frames = _slice_pcm_frames(pcm, sample_rate=sample_rate, channels=channels, frame_ms=frame_ms)
                if frames:
                    with _EARCON_CACHE_LOCK:
                        _EARCON_PCM_CACHE[key] = frames
                    loaded += 1
            except Exception:
                pass
            finally:
                _LOW_CPU_TTS_CTX.reset(tok)
    return loaded


def preload_jarvis_earcons(
    sample_rate: int = 48000,
    channels: int = 1,
    frame_ms: int = 10,
    kinds: list[str] | None = None,
    include_arabic: bool = True,
) -> int:
    """Synchronous wrapper to warm pre-cached JARVIS earcons into memory."""
    try:
        asyncio.get_running_loop()
        # Running inside an active loop; caller should await preload_jarvis_earcons_async.
        return 0
    except RuntimeError:
        return asyncio.run(
            preload_jarvis_earcons_async(
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
                kinds=kinds,
                include_arabic=include_arabic,
            )
        )


