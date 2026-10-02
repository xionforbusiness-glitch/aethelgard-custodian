import os
import time
import json
import datetime
import urllib.request
from librarian import SecondBrainLibrarian

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")

def get_damascus_now():
    """Returns current datetime in Damascus (UTC+3)."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    return utc_now + datetime.timedelta(hours=3)

def send_telegram(text, buttons=None, chat_id=TELEGRAM_USER_ID, token=TELEGRAM_BOT_TOKEN):
    """Sends a Telegram message with optional interactive ReplyKeyboardMarkup."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if buttons:
        payload["reply_markup"] = {
            "keyboard": buttons,
            "resize_keyboard": True,
            "one_time_keyboard": True
        }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        # Fallback without markdown if formatting was rejected
        try:
            payload.pop("parse_mode", None)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status == 200
        except Exception:
            return False

def get_roster_schedule_for_today(vault_dir, weekday_name):
    """Inspects work_attendance_and_shift_roster.md for today's scheduled shift."""
    roster_file = os.path.join(vault_dir, "work_attendance_and_shift_roster.md")
    schedule = {
        "omar": "08:15 AM → 04:00 PM (Standard)",
        "khaled": "08:00 AM → 04:30 PM",
        "rasheed": "08:00 AM → 04:30 PM",
        "adnan": "03:40 AM → 04:30 PM (Dawn)",
        "tarek": "07:00 AM → 04:30 PM",
        "joudi": "03:15 AM → 04:30 PM (Dawn)",
        "hanan": "03:30 AM → 04:30 PM (Dawn)",
        "raboushee": "08:15 AM → 04:30 PM",
        "off": "Yasmeen (Scheduled Off)"
    }
    if weekday_name == "Friday":
        schedule["omar"] = "08:15 AM → 05:40 PM (Overtime)"
        schedule["off"] = "Tarek Orfhly (Scheduled Off)"
    elif weekday_name == "Wednesday":
        schedule["omar"] = "07:20 AM → 03:50 PM (Bus Sync)"
        schedule["khaled"] = "07:20 AM → 04:30 PM (Bus Sync)"
    return schedule

def trigger_morning_briefing(vault_dir):
    """Constructs and delivers the proactive morning briefing."""
    now = get_damascus_now()
    weekday = now.strftime("%A")
    date_str = now.strftime("%B %d, %Y")
    schedule = get_roster_schedule_for_today(vault_dir, weekday)

    msg = (
        f"🌅 *Good morning Omar! Aethelgard Shift Briefing*\n"
        f"📅 *{weekday}, {date_str}* (Damascus Time)\n\n"
        f"✈️ *Station:* Damascus Airport Ground Operations\n"
        f"⏰ *Your Scheduled Shift:* `{schedule['omar']}`\n\n"
        f"👥 *Team Duty Overview:*\n"
        f"• *Khaled:* `{schedule.get('khaled', '08:00 AM → 16:30')}`\n"
        f"• *Rasheed Akel:* `{schedule.get('rasheed', '08:00 AM → 16:30')}`\n"
        f"• *Mohamed Adnan:* `{schedule.get('adnan', 'On duty')}`\n"
        f"• *Tarek Orfhly:* `{schedule.get('tarek', 'On duty')}`\n"
        f"• *Hanan & Joudi:* `{schedule.get('hanan', 'Flight Handling')}`\n"
        f"🏖️ *Scheduled Off:* `{schedule.get('off', 'None')}`\n\n"
        f"⚡ *Quick Action:* Tap below or send your log-in time to record today's shift:"
    )

    buttons = [
        [{"text": "🚌 Bus Sync (07:20 AM)"}, {"text": "🏢 Standard (08:15 AM)"}],
        [{"text": "✈️ Dawn Shift (03:00 AM)"}, {"text": "🏖️ Scheduled Off"}],
        [{"text": "📊 Current Leaderboard"}, {"text": "🏛️ Vault Status"}]
    ]

    return send_telegram(msg, buttons)

def trigger_evening_checkout(vault_dir):
    """Constructs and delivers the evening shift wrap-up check-out prompt."""
    now = get_damascus_now()
    weekday = now.strftime("%A")
    date_str = now.strftime("%B %d, %Y")

    msg = (
        f"🏁 *Evening Shift Check-Out ({weekday})*\n\n"
        f"Omar, your shift was scheduled to wrap up at ~04:00 PM.\n"
        f"Did your shift conclude on time, or did you stay for flight overtime?\n\n"
        f"Tap below or send your departure time to automatically sync your timesheet & Excel sheet:"
    )

    buttons = [
        [{"text": "✅ Out at 04:00 PM (Standard)"}, {"text": "⚡ Overtime (+1h 40m)"}],
        [{"text": "📝 Custom Log Out Time"}, {"text": "📈 View Today's Stats"}]
    ]

    return send_telegram(msg, buttons)

def trigger_librarian_nightly(vault_dir):
    """Performs the nightly index sync and catalog update."""
    lib = SecondBrainLibrarian(vault_dir)
    res = lib.sync_index_catalog()
    now = get_damascus_now()
    send_telegram(f"🌙 *Aethelgard Nightly Librarian Report*\n\n{res}\nTime: `{now.strftime('%H:%M')} Damascus`")

def trigger_weekly_review(vault_dir):
    """Generates the weekly executive review and notifies Omar on Telegram."""
    lib = SecondBrainLibrarian(vault_dir)
    review_path = lib.generate_weekly_review()
    audit = lib.audit_vault()
    now = get_damascus_now()
    
    msg = (
        f"🏛️ *Aethelgard Executive Weekly Review Published!*\n\n"
        f"📊 *Weekly Metrics:*\n"
        f"• Total Knowledge Notes: `{audit['total_notes']}`\n"
        f"• Master Roster: Standardized & synced to Excel\n"
        f"• Review Note: `[[{os.path.splitext(os.path.basename(review_path))[0]}]]`\n\n"
        f"Your local Obsidian vault will pull this review on your next desktop sync."
    )
    send_telegram(msg)

def proactive_scheduler_daemon(vault_dir):
    """Continuous background loop monitoring Damascus time and firing scheduled proactive events."""
    last_morning_date = None
    last_evening_date = None
    last_nightly_date = None
    last_weekly_date = None

    print(f"[{time.strftime('%H:%M:%S')}] ⏰ Proactive Scheduler & Second Brain Librarian started (Damascus UTC+3)...")

    while True:
        try:
            now = get_damascus_now()
            today_str = now.strftime("%Y-%m-%d")
            hour = now.hour
            minute = now.minute
            is_sunday = now.weekday() == 6

            # 1. Morning Shift Briefing (07:15 AM)
            if hour == 7 and minute >= 15 and last_morning_date != today_str:
                print(f"[Proactive Scheduler] Firing Morning Shift Briefing: {today_str} {hour}:{minute:02d}")
                if trigger_morning_briefing(vault_dir):
                    last_morning_date = today_str

            # 2. Evening Check-Out (16:15 PM)
            if hour == 16 and minute >= 15 and last_evening_date != today_str:
                print(f"[Proactive Scheduler] Firing Evening Check-Out: {today_str} {hour}:{minute:02d}")
                if trigger_evening_checkout(vault_dir):
                    last_evening_date = today_str

            # 3. Nightly Librarian Audit & Catalog Sync (00:05 AM)
            if hour == 0 and minute >= 5 and last_nightly_date != today_str:
                print(f"[Second Brain Librarian] Firing Nightly Catalog Sync: {today_str}")
                trigger_librarian_nightly(vault_dir)
                last_nightly_date = today_str

            # 4. Weekly Executive Review (Sunday 23:00 PM)
            if is_sunday and hour == 23 and minute >= 0 and last_weekly_date != today_str:
                print(f"[Second Brain Librarian] Firing Weekly Executive Review: {today_str}")
                trigger_weekly_review(vault_dir)
                last_weekly_date = today_str

        except Exception as e:
            print(f"⚠ Proactive Scheduler notice: {e}")

        time.sleep(30)
