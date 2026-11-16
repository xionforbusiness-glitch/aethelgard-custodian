#!/usr/bin/env python3
"""
⚡ Aethelgard Spontaneous Curiosity Daemon ("Thinking While You're Away")
Stochastically triggers thoughtful check-ins and original ideas during daytime idle hours.
Protected by circadian sleep anchors and anti-spam frequency caps.
"""

import os
import sys
import glob
import json
import random
import datetime
import urllib.request
import urllib.error

from human_mode import load_conversation_state, save_conversation_state, send_human_bursts
from circadian_oracle import get_circadian_forecast
from airport_radar import fetch_damascus_airport_metar

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OMNIROUTE_PORT = os.environ.get("OMNIROUTE_PORT", "20128")
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", os.environ.get("HERMES_CUSTOM_FIRST_TIME_API_KEY", ""))
HERMES_MODEL = os.environ.get("HERMES_MODEL", "antigravity/gemini-3.8-flash-low")


def get_damascus_now():
    """Returns current datetime in Damascus (UTC+3)."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    return utc_now + datetime.timedelta(hours=3)


def should_trigger_spontaneous_ping(vault_dir: str) -> bool:
    """Evaluates whether conditions are right for an unsolicited curiosity ping."""
    state = load_conversation_state()
    if not state.get("spontaneous_pings_enabled", True):
        return False

    now = get_damascus_now()
    today_str = now.strftime("%Y-%m-%d")
    hour = now.hour

    # 1. Daytime Waking Hours Filter (11:00 AM to 21:30 PM only)
    if hour < 11 or hour >= 22:
        return False

    # 2. Daily Frequency Cap (Max 2 pings per day)
    last_ping_date = state.get("last_spontaneous_ping_date", "")
    pings_today = state.get("spontaneous_pings_count_today", 0) if last_ping_date == today_str else 0
    if pings_today >= 2:
        return False

    # 3. Circadian Sleep Anchor Guard (Never disturb rest windows)
    try:
        circadian = get_circadian_forecast(vault_dir)
        sleep_anchor = circadian.get("sleep_anchor", "")
        # If sleep anchor is active (e.g. 20:30 on dawn shift)
        if "20:30" in sleep_anchor and (hour >= 20 or hour < 3):
            return False
    except Exception:
        pass

    # 4. Idle Duration Check (Must be idle for at least 3.5 hours)
    last_interaction = state.get("last_user_interaction", 0)
    idle_seconds = datetime.datetime.now().timestamp() - last_interaction
    min_idle_seconds = 3.5 * 3600  # 3.5 hours

    if idle_seconds < min_idle_seconds:
        return False

    return True


def pick_curiosity_topic(vault_dir: str) -> dict:
    """Picks a knowledge domain or live event from the vault to explore."""
    topics = []

    # Domain 1: Live Damascus Airport Radar
    try:
        metar = fetch_damascus_airport_metar()
        if metar:
            topics.append({
                "type": "aviation_radar",
                "content": f"Damascus Airport live METAR: {metar.get('rawOb', '')}. Category: {metar.get('fltCat', 'VFR')}, Winds: {metar.get('wdir', 0)}° at {metar.get('wspd', 0)} knots."
            })
    except Exception:
        pass

    # Domain 2: Obsidian Vault Concepts / Projects
    concepts_dir = os.path.join(vault_dir, "concepts")
    if os.path.exists(concepts_dir):
        files = glob.glob(os.path.join(concepts_dir, "*.md"))
        if files:
            chosen_file = random.choice(files)
            try:
                with open(chosen_file, "r", encoding="utf-8") as f:
                    content = f.read()[:800]
                    topics.append({
                        "type": "vault_concept",
                        "title": os.path.splitext(os.path.basename(chosen_file))[0],
                        "content": content
                    })
            except Exception:
                pass

    # Domain 3: Flight Simulator Hardware / Flight Dynamics
    topics.append({
        "type": "flight_sim",
        "content": "Microsoft Flight Simulator / Aerofly FS aircraft setup, low-spec laptop graphics optimization, and budget flight controls (rudder pedals, yoke, throttle quadrant)."
    })

    # Domain 4: Speedcubing CFOP & Algorithms
    topics.append({
        "type": "speedcubing",
        "content": "Speedcubing CFOP method, cross efficiency, F2L lookahead, OLL algorithms, and finger-trick mechanics."
    })

    return random.choice(topics) if topics else topics[0]


def generate_spontaneous_thought(vault_dir: str) -> str:
    """Uses Google Antigravity Pro Plan via OmniRoute to synthesize a casual, natural, spontaneous curiosity thought."""
    topic = pick_curiosity_topic(vault_dir)

    prompt = (
        f"You are Aethelgard, chatting with Omar Elnemr on Telegram.\n"
        f"Omar has been away for a few hours. You are casually reaching out with an unsolicited, original thought or interesting question based on this topic:\n"
        f"{json.dumps(topic)}\n\n"
        f"Guidelines:\n"
        f"1. Be concise (2-3 short conversational sentences max).\n"
        f"2. Tone: Warm, curious, natural peer/companion (like a friend texting). Absolutely NO robotic phrases ('Certainly', 'As your assistant', 'I hope this finds you well').\n"
        f"3. Start casually (e.g. 'Hey Omar, random thought while you were away...', 'Was just thinking about...').\n"
        f"4. End with a light, non-demanding question or observation.\n"
        f"Output ONLY the text to send Omar."
    )

    url = f"http://127.0.0.1:{OMNIROUTE_PORT}/v1/chat/completions"
    payload = {
        "model": HERMES_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 256
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OMNIROUTE_API_KEY}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            thought = res.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if thought:
                return thought
    except Exception as e:
        print(f"⚠ Antigravity Pro curiosity generation notice: {e}")

    # Fallback thoughts if offline
    fallbacks = [
        "Hey Omar, random thought while you were away—was thinking about that PC build for flight sims. If we hunt for used AM4 parts in Damascus, we might be able to afford a proper flight stick without blowing the budget. What do you think?",
        "Hey Omar, was just looking at your notes on the Damascus runway approaches. On the 23R approach with those afternoon winds, do you usually notice more thermal turbulence or steady crosswinds?",
        "Quick thought while you're away—was reviewing some F2L lookahead strategies. Turns out slowing down TPS slightly cuts pause time by almost 30%. Might be worth testing on your next timed session!"
    ]
    return random.choice(fallbacks)


def trigger_spontaneous_ping(vault_dir: str, force: bool = False) -> bool:
    """Executes a spontaneous ping if conditions are met."""
    if not force and not should_trigger_spontaneous_ping(vault_dir):
        return False

    now = get_damascus_now()
    today_str = now.strftime("%Y-%m-%d")

    thought = generate_spontaneous_thought(vault_dir)
    print(f"[Spontaneous Curiosity] Delivering ping at {now.strftime('%H:%M Damascus')}: {thought[:60]}...")

    buttons = [
        [{"text": "☕ Casual Mode"}, {"text": "🏛️ Custodian Mode"}],
        [{"text": "📞 Call Aethelgard"}, {"text": "✈️ Airport Radar"}]
    ]

    # Stochastically alternate between pure British voice memo (no text) and snappy text bursts
    send_as_voice = random.choice([True, False])
    if send_as_voice:
        try:
            from voice_engine import send_telegram_voice_sync
            print(f"[Spontaneous Curiosity] Delivering spontaneous audio voice memo (no text caption)...")
            sent = send_telegram_voice_sync(thought, caption=None)
        except Exception as ve:
            sent = send_human_bursts(thought, buttons=buttons)
    else:
        sent = send_human_bursts(thought, buttons=buttons)

    # Update state
    state = load_conversation_state()
    last_date = state.get("last_spontaneous_ping_date", "")
    pings_today = state.get("spontaneous_pings_count_today", 0) if last_date == today_str else 0
    state["last_spontaneous_ping_date"] = today_str
    state["spontaneous_pings_count_today"] = pings_today + 1
    save_conversation_state(state)

    return sent
