#!/usr/bin/env python3
"""
🎭 Aethelgard Human Conversational Engine & Burst Delivery
Enables switching between:
  - 🏛️ Custodian Mode: Structured executive tables, flight radar, timesheets, and technical markdown.
  - ☕ Casual / Human Mode: Rapid text bursts, realistic typing indicators, casual phrasing, zero AI boilerplate.
"""

import os
import re
import time
import json
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "conversation_state.json")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")


def load_conversation_state() -> dict:
    """Loads persistent conversation state (mode, timestamps, settings)."""
    default_state = {
        "mode": "custodian",               # "custodian" or "human"
        "spontaneous_pings_enabled": True, # Stochastic curiosity check-ins
        "last_user_interaction": time.time(),
        "last_spontaneous_ping_date": "",
        "spontaneous_pings_count_today": 0
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_state.update(data)
        except Exception:
            pass
    return default_state


def save_conversation_state(state: dict) -> None:
    """Saves conversation state to disk."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"⚠ Failed to save conversation state: {e}")


def get_conversation_mode() -> str:
    """Returns current active mode ('custodian' or 'human')."""
    return load_conversation_state().get("mode", "custodian")


def set_conversation_mode(mode: str) -> str:
    """Sets mode to 'custodian' or 'human'."""
    clean_mode = "human" if "human" in mode.lower() or "casual" in mode.lower() else "custodian"
    state = load_conversation_state()
    state["mode"] = clean_mode
    save_conversation_state(state)
    return clean_mode


def record_user_interaction() -> None:
    """Updates the last user interaction timestamp to prevent unwanted spontaneous pings."""
    state = load_conversation_state()
    state["last_user_interaction"] = time.time()
    save_conversation_state(state)


def send_chat_action(action: str = "typing", chat_id=TELEGRAM_USER_ID, token=TELEGRAM_BOT_TOKEN) -> bool:
    """Sends Telegram chat action (e.g. 'typing', 'record_voice')."""
    url = f"https://api.telegram.org/bot{token}/sendChatAction"
    data = json.dumps({"chat_id": chat_id, "action": action}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def split_into_human_bursts(text: str) -> list:
    """
    Splits a longer response into natural, human conversational text bursts.
    Preserves code blocks and links intact.
    """
    clean = text.strip()
    if not clean:
        return []

    # If message contains code blocks, do not aggressively split
    if "```" in clean:
        parts = clean.split("```")
        bursts = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # Code block
                bursts.append(f"```{part}```")
            else:
                sub = part.strip()
                if sub:
                    bursts.extend([p.strip() for p in re.split(r'\n{2,}', sub) if p.strip()])
        return bursts if bursts else [clean]

    # Split into paragraphs or sentences
    paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]
    bursts = []

    for p in paragraphs:
        # If paragraph is short (< 160 chars), keep as single burst
        if len(p) <= 160:
            bursts.append(p)
        else:
            # Split by sentence boundaries (.!?) if paragraph is long
            sentences = re.split(r'(?<=[.!?])\s+', p)
            current_burst = ""
            for s in sentences:
                if len(current_burst) + len(s) < 180:
                    current_burst = f"{current_burst} {s}".strip()
                else:
                    if current_burst:
                        bursts.append(current_burst)
                    current_burst = s
            if current_burst:
                bursts.append(current_burst)

    # Cap to max 4 bursts per response so Telegram doesn't get flooded
    if len(bursts) > 4:
        # Group remaining
        condensed = bursts[:3]
        condensed.append(" ".join(bursts[3:]))
        bursts = condensed

    return bursts if bursts else [clean]


def send_human_bursts(text: str, buttons=None, chat_id=TELEGRAM_USER_ID, token=TELEGRAM_BOT_TOKEN) -> bool:
    """
    Delivers a message in human typing bursts:
    1. Sends 'typing...' status.
    2. Calculates human delay based on chunk length (~50-60 wpm typing speed).
    3. Drops message bubble.
    4. Attaches interactive keyboard on the final bubble.
    """
    bursts = split_into_human_bursts(text)
    if not bursts:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    success = True

    for i, burst in enumerate(bursts):
        is_last = (i == len(bursts) - 1)

        # 1. Send typing indicator
        send_chat_action("typing", chat_id=chat_id, token=token)

        # 2. Human typing latency: ~30-40 chars per second + reading pause
        delay = min(max(len(burst) * 0.025, 0.8), 2.8)
        time.sleep(delay)

        # 3. Construct payload
        payload = {
            "chat_id": chat_id,
            "text": burst,
            "parse_mode": "Markdown"
        }
        if is_last and buttons:
            payload["reply_markup"] = {
                "keyboard": buttons,
                "resize_keyboard": True,
                "one_time_keyboard": True
            }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                pass
        except Exception:
            # Retry without markdown if formatting was rejected
            try:
                payload.pop("parse_mode", None)
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    pass
            except Exception:
                success = False

        # Brief natural pause between bubbles (400ms)
        if not is_last:
            time.sleep(0.4)

    return success
