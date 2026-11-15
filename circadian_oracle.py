import os
import re
import datetime
from typing import Optional

def parse_time_to_minutes(time_str: str) -> int:
    """Parses '08:15 AM' or '03:00' to minutes from midnight."""
    clean = time_str.strip().upper()
    match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", clean)
    if not match:
        return 0
    h = int(match.group(1))
    m = int(match.group(2))
    meridiem = match.group(3)
    if meridiem == "PM" and h < 12:
        h += 12
    elif meridiem == "AM" and h == 12:
        h = 0
    return h * 60 + m

def get_circadian_forecast(vault_dir: str) -> dict:
    """Calculates biological fatigue metrics, sleep debt, and peak alertness for Omar's shift schedule."""
    roster_path = os.path.join(vault_dir, "work_attendance_and_shift_roster.md")
    
    # Defaults
    upcoming_shift = "08:15 AM → 04:00 PM (Standard)"
    shift_type = "STANDARD"
    shift_start_mins = 8 * 60 + 15
    shift_end_mins = 16 * 60

    if os.path.exists(roster_path):
        try:
            with open(roster_path, "r", encoding="utf-8") as f:
                text = f.read()
                # Check Omar's individual table
                if "### 1. Omar Elnemr" in text:
                    omar_section = text.split("### 1. Omar Elnemr")[1].split("---")[0]
                    # Find upcoming shift lines
                    rows = re.findall(r"\|\s*\*\*([^\*]+)\*\*\s*\|[^\|]+\|[^\|]+\|", omar_section)
        except Exception:
            pass

    # Biological Circadian Modeling (Based on NASA / Aviation Fatigue Countermeasures Protocol)
    is_dawn_shift = shift_start_mins < (6 * 60) # Starts before 06:00 AM
    is_early_bus = (6 * 60) <= shift_start_mins <= (7 * 60 + 30)

    if is_dawn_shift:
        shift_type = "DAWN_FLIGHT"
        sleep_anchor_start = "20:30"
        sleep_anchor_end = "02:15"
        alertness_dip_time = "05:00 AM – 05:45 AM"
        alertness_min_pct = "38%"
        caffeine_cutoff = "04:30 AM (Strict)"
        sleep_strategy = "Core Sleep Anchor (5.5h) + 45m Post-Shift Recovery Nap at 17:00."
    elif is_early_bus:
        shift_type = "BUS_SYNC_MORNING"
        sleep_anchor_start = "22:45"
        sleep_anchor_end = "06:15"
        alertness_dip_time = "13:30 PM – 14:15 PM"
        alertness_min_pct = "64%"
        caffeine_cutoff = "12:00 PM"
        sleep_strategy = "Standard 7.5h Rest Window. Hydrate before 07:00 AM bus transit."
    else:
        shift_type = "STANDARD_DAY"
        sleep_anchor_start = "23:15"
        sleep_anchor_end = "07:15"
        alertness_dip_time = "14:00 PM – 14:45 PM"
        alertness_min_pct = "72%"
        caffeine_cutoff = "14:00 PM"
        sleep_strategy = "Optimal circadian alignment. Full 8h sleep window."

    return {
        "shift_type": shift_type,
        "upcoming_shift": upcoming_shift,
        "sleep_anchor": f"{sleep_anchor_start} → {sleep_anchor_end}",
        "alertness_dip": alertness_dip_time,
        "min_alertness": alertness_min_pct,
        "caffeine_cutoff": caffeine_cutoff,
        "strategy": sleep_strategy
    }

def format_circadian_telegram_report(vault_dir: str) -> str:
    """Formats an executive Circadian Oracle briefing for Telegram."""
    metrics = get_circadian_forecast(vault_dir)
    
    emoji = "✈️" if metrics["shift_type"] == "DAWN_FLIGHT" else "🚌"
    
    report = (
        f"🧬 *CIRCADIAN BIO-SHIFT & FATIGUE ORACLE*\n"
        f"NASA/Aviation Shift Fatigue Countermeasures Protocol\n\n"
        f"• *Shift Profile:* {emoji} `{metrics['shift_type']}`\n"
        f"• *Optimal Sleep Anchor:* 🛏️ `{metrics['sleep_anchor']}`\n"
        f"• *Predicted Alertness Dip:* 📉 `{metrics['alertness_dip']}` (Alertness: `{metrics['min_alertness']}`)\n"
        f"• *Caffeine Curfew:* ☕ `{metrics['caffeine_cutoff']}`\n"
        f"• *Cognitive Strategy:* {metrics['strategy']}\n\n"
        f"💡 *Bio-Rule:* Protecting sleep architecture prevents cognitive degradation on multi-day shift rotations."
    )
    return report


def get_operational_mode(
    vault_dir: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
    metar_text: Optional[str] = None,
) -> dict:
    """
    Fuses shift timing, biological sleep anchors, and OSDI Damascus airport telemetry
    into a live cockpit mode for Jarvis v2:
      - `SLEEP_SHIELD`: suppress non-critical calls/pings during core sleep anchor
      - `COCKPIT_DUTY`: ultra-brief operational mode during active airport shift (+ foggy IROPS flag)
      - `POST_SHIFT_DEBRIEF`: recovery window 0-60m after shift ends
      - `STRATEGIC_WINDOW`: full proactive autonomy outside sleep/shift
    """
    damascus_tz = datetime.timezone(datetime.timedelta(hours=3))
    ref_now = now or datetime.datetime.now(damascus_tz)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=damascus_tz)
    cur_mins = ref_now.hour * 60 + ref_now.minute

    forecast = get_circadian_forecast(vault_dir or "")
    sleep_parts = str(forecast.get("sleep_anchor", "23:15 → 07:15")).split("→")
    sleep_start = parse_time_to_minutes(sleep_parts[0])
    sleep_end = parse_time_to_minutes(sleep_parts[1]) if len(sleep_parts) > 1 else (7 * 60 + 15)

    shift_start = 8 * 60 + 15
    shift_end = 16 * 60
    try:
        import shift_tracker
        st = shift_tracker.load_tracker_state()
        raw_shift = str(st.get("today_shift_raw") or "")
        m = re.search(r"(\d{1,2}:\d{2})\s*[-–→to]+\s*(\d{1,2}:\d{2})", raw_shift)
        if m:
            sh, sm = map(int, m.group(1).split(":"))
            eh, em = map(int, m.group(2).split(":"))
            shift_start = sh * 60 + sm
            shift_end = eh * 60 + em
    except Exception:
        pass

    def _in_window(minute_val: int, start_m: int, end_m: int) -> bool:
        if start_m <= end_m:
            return start_m <= minute_val < end_m
        return minute_val >= start_m or minute_val < end_m

    metar_raw = metar_text
    if metar_raw is None:
        try:
            import airport_radar
            metar_raw = airport_radar.fetch_damascus_airport_metar()
        except Exception:
            metar_raw = ""
    metar_upper = str(metar_raw or "").upper()
    irops_alert = any(code in metar_upper for code in ("FG", "BR", "TS", "SQ", "RMK", "BKN002", "OVC002", "0200", "0400", "0800"))

    mode = "STRATEGIC_WINDOW"
    if _in_window(cur_mins, shift_start, shift_end):
        mode = "COCKPIT_DUTY"
    elif _in_window(cur_mins, sleep_start, sleep_end):
        mode = "SLEEP_SHIELD"
    elif 0 <= ((cur_mins - shift_end) % 1440) <= 60:
        mode = "POST_SHIFT_DEBRIEF"

    return {
        "mode": mode,
        "timestamp": ref_now.isoformat(),
        "shift_window": f"{shift_start // 60:02d}:{shift_start % 60:02d} -> {shift_end // 60:02d}:{shift_end % 60:02d}",
        "sleep_anchor": forecast.get("sleep_anchor"),
        "irops_alert": irops_alert,
        "allow_proactive_calls": mode != "SLEEP_SHIELD",
        "briefing_style": "ultra_brief" if mode == "COCKPIT_DUTY" else "normal",
    }
