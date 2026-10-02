#!/usr/bin/env python3
"""
⚡ Aethelgard Gemini Speech-To-Text Engine
Uses Google Gemini 3.8 Flash for ultra-fast, zero-RAM voice note transcription.
Designed for Hermes Agent local_command STT backend.
"""

import os
import sys
import json
import base64
import argparse
import urllib.request
import urllib.error

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "AQ.Ab8RN6I64Bj-z2kKo2b-o6cETMuJgQ0LySbUjAMqgraCrYKPzQ")

def transcribe(input_path: str, output_dir: str = None) -> str:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Audio file not found: {input_path}")

    # Read audio bytes
    with open(input_path, "rb") as f:
        audio_bytes = f.read()

    # Determine mime type
    ext = os.path.splitext(input_path)[1].lower()
    mime = "audio/ogg" if ext in (".ogg", ".oga", ".opus") else "audio/mp3"

    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={GOOGLE_API_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {
                    "text": "Transcribe exactly what is spoken in this audio file verbatim. Output only the transcript."
                },
                {
                    "inline_data": {
                        "mime_type": mime,
                        "data": b64_audio
                    }
                }
            ]
        }]
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            transcript = res.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
    except Exception as e:
        transcript = f"[Transcription error: {e}]"

    # If output_dir is specified, write transcript.txt for Hermes local_command
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        out_file = os.path.join(output_dir, "transcript.txt")
        with open(out_file, "w", encoding="utf-8") as out_f:
            out_f.write(transcript)

    return transcript

def main():
    parser = argparse.ArgumentParser(description="Gemini STT for Hermes Agent")
    parser.add_argument("input_path", help="Path to input audio file")
    parser.add_argument("--output_dir", "-o", default=None, help="Directory to save transcript.txt")
    parser.add_argument("--model", default=None, help="Ignored (for Hermes CLI template compatibility)")
    parser.add_argument("--language", default=None, help="Ignored (for Hermes CLI template compatibility)")
    parser.add_argument("--output_format", default="txt", help="Ignored (for Hermes CLI template compatibility)")
    args, unknown = parser.parse_known_args()

    # In case Hermes passes output_dir as positional or kwargs
    output_dir = args.output_dir
    if not output_dir and len(sys.argv) > 2 and not sys.argv[2].startswith("-"):
        output_dir = sys.argv[2]

    transcript = transcribe(args.input_path, output_dir)
    print(transcript)

if __name__ == "__main__":
    main()
