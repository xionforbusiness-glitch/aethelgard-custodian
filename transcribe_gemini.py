#!/usr/bin/env python3
"""
⚡ Aethelgard Antigravity Pro Speech-To-Text Engine
Uses Google Antigravity Pro Plan (daily-cloudcode-pa.googleapis.com) with Gemini 3.8 Flash
for ultra-fast, zero-RAM voice note transcription with multi-model failover and bilingual
(Arabic/English) support. Zero external AI Studio free keys required.
"""

import os
import sys
import json
import time
import base64
import argparse
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from antigravity_token_manager import fetch_fresh_google_token

# Priority list of live Antigravity Pro models with native audio transcription capability
ANTIGRAVITY_STT_MODELS = [
    "gemini-3.8-flash-low",
    "gemini-3.8-flash-medium",
    "gemini-3.5-flash-low",
    "gemini-3.1-flash-lite",
]


def transcribe_via_antigravity(model_name: str, mime: str, b64_audio: str, access_token: str) -> str:
    """Transcribes audio directly via Google Antigravity Cloud Code SSE endpoint."""
    url = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "antigravity/ide/2.21.1 darwin/arm64"
    }
    body = {
        "model": model_name,
        "project": "aicode-consumers",
        "userAgent": "antigravity",
        "requestType": "agent",
        "requestId": f"stt/{int(time.time() * 1000)}",
        "request": {
            "sessionId": f"-{int(time.time() * 1000)}",
            "contents": [{
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "You are an expert audio transcription system. "
                            "Transcribe the spoken audio verbatim in its original language (Arabic, English, or mixed). "
                            "Do not translate, summarize, or add explanations. "
                            "Output ONLY the verbatim text spoken in the audio."
                        )
                    },
                    {
                        "inlineData": {
                            "mimeType": mime,
                            "data": b64_audio
                        }
                    }
                ]
            }],
            "generationConfig": {
                "maxOutputTokens": 2048,
                "thinkingConfig": {"thinkingBudget": 128}
            }
        }
    }

    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw_sse = resp.read().decode("utf-8", errors="replace")

    reply_text = ""
    for line in raw_sse.splitlines():
        if line.startswith("data: "):
            try:
                j = json.loads(line[6:])
                candidates = j.get("response", {}).get("candidates", [])
                for c in candidates:
                    for p in c.get("content", {}).get("parts", []):
                        if "text" in p and not p.get("thought"):
                            reply_text += p["text"]
            except Exception:
                pass

    text = reply_text.strip()
    if not text:
        raise ValueError(f"Empty transcript returned by Antigravity model {model_name}")
    return text


def transcribe(input_path: str, output_dir: str = None) -> str:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Audio file not found: {input_path}")

    with open(input_path, "rb") as f:
        audio_bytes = f.read()

    ext = os.path.splitext(input_path)[1].lower()
    if ext in (".ogg", ".oga", ".opus"):
        mime = "audio/ogg"
    elif ext in (".mp3",):
        mime = "audio/mp3"
    elif ext in (".wav",):
        mime = "audio/wav"
    elif ext in (".m4a", ".aac"):
        mime = "audio/mp4"
    else:
        mime = "audio/ogg"

    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")

    access_token, _ = fetch_fresh_google_token()
    transcript = None
    last_error = None

    if access_token:
        for model_name in ANTIGRAVITY_STT_MODELS:
            for attempt in range(2):
                try:
                    transcript = transcribe_via_antigravity(model_name, mime, b64_audio, access_token)
                    if transcript:
                        break
                except urllib.error.HTTPError as he:
                    last_error = f"HTTP {he.code} on {model_name}"
                    time.sleep(1)
                except Exception as e:
                    last_error = f"{type(e).__name__}: {e} on {model_name}"
                    time.sleep(1)
            if transcript:
                break

    if not transcript:
        raise RuntimeError(f"All Antigravity STT models failed. Last error: {last_error}")

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        txt_path = os.path.join(output_dir, f"{base_name}.txt")
        with open(txt_path, "w", encoding="utf-8") as out_f:
            out_f.write(transcript)

    return transcript


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transcribe audio using Google Antigravity Pro Gemini 3.8 Flash")
    parser.add_argument("input_path", help="Path to input audio file")
    parser.add_argument("--output_dir", default=None, help="Optional directory to write .txt output")
    args = parser.parse_args()

    try:
        result = transcribe(args.input_path, args.output_dir)
        print(result)
    except Exception as exc:
        sys.stderr.write(f"Transcription error: {exc}\n")
        sys.exit(1)
