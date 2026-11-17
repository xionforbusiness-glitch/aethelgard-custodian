#!/usr/bin/env python3
"""
📎 Aethelgard General-Purpose Telegram Document Sender
Usage:
    python3 send_telegram_file.py /path/to/file.pdf --caption "Optional caption"
"""

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid


def _load_env_fallback(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.expanduser("~/.hermes/profiles/llm-wiki/.env"),
    ]
    for env_path in candidates:
        if os.path.isfile(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as ef:
                    for line in ef:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            if k.strip() == key:
                                return v.strip().strip("'\"")
            except Exception:
                pass
    return ""


def send_file_to_telegram(file_path: str, caption: str = "", chat_id: str = "") -> dict:
    abs_path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {abs_path}")

    token = _load_env_fallback("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured in environment or .env")

    target_chat = (chat_id or _load_env_fallback("TELEGRAM_ALLOWED_USERS") or "").split(",")[0].strip()
    if not target_chat:
        raise RuntimeError("TELEGRAM_ALLOWED_USERS / chat_id is not configured")

    filename = os.path.basename(abs_path)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    boundary = f"----AethelgardBoundary{uuid.uuid4().hex}"

    parts = []
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{target_chat}\r\n"
    )
    if caption:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption}\r\n"
        )

    with open(abs_path, "rb") as fh:
        file_bytes = fh.read()

    head = "".join(parts).encode("utf-8") + (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + file_bytes + tail

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_txt = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API HTTP {exc.code}: {err_txt}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a file directly to Omar on Telegram.")
    parser.add_argument("file_path", help="Path to the file to send (PDF, DOCX, XLSX, etc.)")
    parser.add_argument("--caption", "-c", default="", help="Optional Telegram caption")
    parser.add_argument("--chat-id", default="", help="Override Telegram chat_id")
    args = parser.parse_args()

    try:
        res = send_file_to_telegram(args.file_path, caption=args.caption, chat_id=args.chat_id)
        if res.get("ok"):
            msg_id = (res.get("result") or {}).get("message_id")
            print(f"✅ Sent '{os.path.basename(args.file_path)}' to Telegram (message_id={msg_id}).")
            return 0
        print(f"❌ Telegram API rejected document: {res}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"❌ Failed to send file to Telegram: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
