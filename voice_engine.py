import os
import re
import uuid
import json
import asyncio
import tempfile
import urllib.request
import edge_tts

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")

VOICE_ENGLISH = "en-GB-RyanNeural"  # Paul Bettany / Classic British Jarvis
VOICE_ARABIC = "ar-SY-LaithNeural"    # Authentic Syrian Male
JARVIS_PITCH = "-4Hz"                 # Deeper, calm resonance
JARVIS_RATE = "-2%"                   # Measured cadence

def is_arabic(text: str) -> bool:
    """Checks if text contains substantial Arabic characters."""
    arabic_chars = re.findall(r"[\u0600-\u06FF]", text)
    return len(arabic_chars) > len(text) * 0.2

async def generate_voice_file(text: str, output_path: str, voice: str = "auto") -> str:
    """Generates an audio voice note file using edge-tts with Jarvis calibration."""
    is_ar = is_arabic(text)
    if voice == "auto":
        voice = VOICE_ARABIC if is_ar else VOICE_ENGLISH

    clean_text = re.sub(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
    clean_text = re.sub(r"[*_`#~]", "", clean_text)
    clean_text = clean_text.strip()
    if not clean_text:
        clean_text = "Task completed, sir."

    pitch = "-1Hz" if is_ar else JARVIS_PITCH
    rate = "+0%" if is_ar else JARVIS_RATE

    communicate = edge_tts.Communicate(clean_text, voice, pitch=pitch, rate=rate)
    await communicate.save(output_path)
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
