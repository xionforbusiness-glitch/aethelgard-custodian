#!/usr/bin/env python3
"""
📋 Aethelgard Shift & Attendance Autonomous Tracker
Implements Omar's exact attendance rules:
1. Morning reminders are non-intrusive notices (no action buttons).
2. If Omar replies to the bot during the day -> Today is marked ON duty.
3. If Omar does not reply at all -> Today is automatically marked OFF (scheduled day off).
4. Direct Excel sheet delivery on request to Telegram via sendDocument.
"""

import os
import sys
import json
import uuid
import datetime
import urllib.request
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "shift_attendance_tracker.json")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")
VAULT_DIR = os.path.abspath(os.path.expanduser(os.environ.get("VAULT_DIR", "~/vault")))


def get_damascus_now():
    """Returns current datetime in Damascus (UTC+3)."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    return utc_now + datetime.timedelta(hours=3)


def load_tracker_state() -> dict:
    today_str = get_damascus_now().strftime("%Y-%m-%d")
    default_state = {
        "date": today_str,
        "status": "pending",  # 'pending', 'on', 'off'
        "reminder_sent_at": None,
        "first_reply_at": None,
        "logged_to_vault": False,
        "notes": ""
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    default_state.update(data)
                else:
                    # New day, reset
                    save_tracker_state(default_state)
        except Exception:
            pass
    return default_state


def save_tracker_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"⚠ Failed to save shift tracker state: {e}", flush=True)


def on_morning_reminder_sent():
    """Called when the 06:00 AM or 07:15 AM reminder fires."""
    state = load_tracker_state()
    now_str = get_damascus_now().strftime("%H:%M")
    state["reminder_sent_at"] = now_str
    if state["status"] not in ("on", "off"):
        state["status"] = "pending"
    save_tracker_state(state)
    print(f"[Shift Tracker] Morning reminder logged at {now_str}. Status: PENDING (awaiting reply)", flush=True)


def on_user_interaction(vault_dir=VAULT_DIR, message_text=""):
    """
    Called whenever Omar sends any message to Telegram.
    If status is 'pending', transitions to 'on' and records duty.
    """
    state = load_tracker_state()
    if state.get("status") == "pending":
        now = get_damascus_now()
        now_str = now.strftime("%I:%M %p")
        state["status"] = "on"
        state["first_reply_at"] = now_str
        save_tracker_state(state)
        print(f"[Shift Tracker] 🌟 Omar replied at {now_str}! Marking today as ON duty.", flush=True)
        sync_shift_to_vault(vault_dir=vault_dir, status="ON", check_in_time=now_str)
        return True
    return False


def on_evening_check(vault_dir=VAULT_DIR) -> str:
    """
    Called at evening checkout time (16:15 PM).
    If still 'pending', Omar never replied -> marked as 'off'.
    Returns final status ('on' or 'off').
    """
    state = load_tracker_state()
    if state.get("status") == "pending":
        state["status"] = "off"
        save_tracker_state(state)
        print("[Shift Tracker] 🏖️ No reply received today. Automatically marking as OFF (day off).", flush=True)
        sync_shift_to_vault(vault_dir=vault_dir, status="OFF", check_in_time=None)
        return "off"
    return state.get("status", "off")


def sync_shift_to_vault(vault_dir=VAULT_DIR, status="ON", check_in_time=None):
    """
    Updates work_attendance_and_shift_roster.md and the Excel file in the Obsidian vault.
    """
    now = get_damascus_now()
    date_str = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A")
    roster_md = os.path.join(vault_dir, "work_attendance_and_shift_roster.md")

    if not os.path.exists(vault_dir):
        return

    # Update Markdown Roster
    if os.path.exists(roster_md):
        try:
            with open(roster_md, "r", encoding="utf-8") as f:
                content = f.read()

            row_entry = (
                f"| {date_str} | {weekday} | Omar Elnemr | {check_in_time or '-'} | 04:00 PM | "
                f"{'7h 45m' if status == 'ON' else '0h 00m'} | {'7.75' if status == 'ON' else '0.0'} | "
                f"{'Standard' if status == 'ON' else 'Scheduled Off'} | "
                f"{'Confirmed via Telegram' if status == 'ON' else 'No reply recorded - marked off'} |\n"
            )

            # If date already in content, avoid duplicate appending
            if f"| {date_str} |" not in content:
                with open(roster_md, "a", encoding="utf-8") as f:
                    f.write(row_entry)
                print(f"[Shift Tracker] Appended {date_str} ({status}) to {roster_md}", flush=True)
        except Exception as e:
            print(f"⚠ Failed to update markdown roster: {e}", flush=True)

    # Sync Excel File
    sync_excel_file(vault_dir, date_str, weekday, status, check_in_time)


def sync_excel_file(vault_dir, date_str, weekday, status, check_in_time):
    """Appends the record to the standardized Excel spreadsheet."""
    excel_path = os.path.join(vault_dir, "attendance_september_2026_standardized.xlsx")
    if not os.path.exists(excel_path):
        excel_path = os.path.join(vault_dir, "attendance_september_2026.xlsx")
    if not os.path.exists(excel_path):
        return

    try:
        import openpyxl
        wb = openpyxl.load_workbook(excel_path)
        ws = wb["Detailed Timesheet"] if "Detailed Timesheet" in wb.sheetnames else wb.active
        
        # Check if today already exists
        exists = False
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and row[0] == date_str and row[2] == "Omar Elnemr":
                exists = True
                break
        
        if not exists:
            log_in = check_in_time or "-"
            log_out = "04:00 PM" if status == "ON" else "-"
            dur_fmt = "7h 45m" if status == "ON" else "0h 00m"
            dec_hrs = 7.75 if status == "ON" else 0.0
            stat_lbl = "Standard" if status == "ON" else "Off"
            notes = "Confirmed via Telegram reply" if status == "ON" else "Automatic: No reply recorded (Day Off)"
            
            ws.append([date_str, weekday, "Omar Elnemr", log_in, log_out, dur_fmt, dec_hrs, stat_lbl, notes])
            wb.save(excel_path)
            print(f"[Shift Tracker] Appended {date_str} to Excel sheet: {excel_path}", flush=True)
    except Exception as e:
        print(f"⚠ Failed to sync Excel sheet: {e}", flush=True)


def find_latest_excel_sheet(vault_dir=VAULT_DIR) -> str:
    """Finds the primary attendance or financial Excel sheet in the vault or current repo."""
    candidates = [
        os.path.join(vault_dir, "attendance_september_2026_standardized.xlsx"),
        os.path.join(vault_dir, "attendance_september_2026.xlsx"),
        os.path.join(SCRIPT_DIR, "attendance_september_2026_standardized.xlsx")
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    # Search vault directory
    if os.path.exists(vault_dir):
        for root, dirs, files in os.walk(vault_dir):
            for f in files:
                if f.endswith(".xlsx"):
                    return os.path.join(root, f)
    return None


def send_excel_to_telegram(chat_id=TELEGRAM_USER_ID, token=TELEGRAM_BOT_TOKEN, vault_dir=VAULT_DIR, caption=None) -> bool:
    """Delivers the master Excel sheet directly to Omar's Telegram chat via sendDocument."""
    file_path = find_latest_excel_sheet(vault_dir)
    if not file_path or not os.path.exists(file_path):
        print("⚠ No Excel sheet found to send.", flush=True)
        return False

    boundary = uuid.uuid4().hex
    filename = os.path.basename(file_path)
    cap = caption or "📊 Aethelgard Master Attendance & Shift Roster (Excel Spreadsheet)"

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        part1 = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"chat_id\"\r\n\r\n"
            f"{chat_id}\r\n"
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"caption\"\r\n\r\n"
            f"{cap}\r\n"
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
        ).encode("utf-8")

        part2 = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = part1 + file_bytes + part2

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            success = resp.status == 200
            if success:
                print(f"[Shift Tracker] 📱 Delivered Excel file {filename} to Telegram user {chat_id}!", flush=True)
            return success
    except Exception as e:
        print(f"⚠ Failed to send Excel document to Telegram: {e}", flush=True)
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "send_excel":
        send_excel_to_telegram()
    elif len(sys.argv) > 1 and sys.argv[1] == "reply":
        on_user_interaction()
    else:
        print("Shift Tracker status:", load_tracker_state())
