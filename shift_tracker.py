#!/usr/bin/env python3
"""
📋 Aethelgard Shift & Attendance Autonomous Tracker
Implements Omar's exact attendance rules:
1. Morning reminders are non-intrusive notices (no action buttons).
2. If Omar replies to the bot during the day -> Today is marked ON duty.
3. If Omar does not reply at all -> Today is automatically marked OFF (scheduled day off).
4. Direct Excel sheet delivery on request to Telegram via sendDocument, using the standardized
   table style (omar_elnemr_attendance_october_2026.xlsx).
"""

import os
import re
import sys
import json
import uuid
import datetime
import urllib.request
import subprocess

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "shift_attendance_tracker.json")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")
VAULT_DIR = os.path.abspath(os.path.expanduser(os.environ.get("VAULT_DIR", "~/vault")))


def get_damascus_now():
    """Returns current datetime in Damascus (UTC+3)."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    return utc_now + datetime.timedelta(hours=3)


def parse_shift_times_from_text(text: str):
    """
    Parses natural-language shift logs like:
      'Logged in 8am and logged off 5:05pm'
      'logged in 7:55am and logged out 4:30 pm'
    Returns (log_in_str, log_out_str, dur_fmt, dec_hrs) or None.
    """
    if not text:
        return None
    pattern = re.compile(
        r"logged\s+in\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?.*?"
        r"logged\s+(?:out|off)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        re.IGNORECASE | re.DOTALL
    )
    m = pattern.search(text)
    if not m:
        return None

    h1, m1, ap1, h2, m2, ap2 = m.groups()
    h1, m1 = int(h1), int(m1 or 0)
    h2, m2 = int(h2), int(m2 or 0)
    ap1 = (ap1 or "am").upper()
    ap2 = (ap2 or "pm").upper()

    log_in_str = f"{h1:02d}:{m1:02d} {ap1}"
    log_out_str = f"{h2:02d}:{m2:02d} {ap2}"

    h1_24 = (h1 % 12) + (12 if ap1 == "PM" else 0)
    h2_24 = (h2 % 12) + (12 if ap2 == "PM" else 0)
    total_mins = (h2_24 * 60 + m2) - (h1_24 * 60 + m1)
    if total_mins <= 0:
        total_mins += 24 * 60

    dh, dm = divmod(total_mins, 60)
    dur_fmt = f"{dh}h {dm:02d}m"
    dec_hrs = round(total_mins / 60.0, 2)
    return log_in_str, log_out_str, dur_fmt, dec_hrs


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
    Also parses explicit 'logged in ... logged out/off ...' timestamps if provided.
    """
    state = load_tracker_state()
    parsed = parse_shift_times_from_text(message_text)
    if state.get("status") == "pending" or parsed:
        now = get_damascus_now()
        now_str = parsed[0] if parsed else now.strftime("%I:%M %p")
        log_out_str = parsed[1] if parsed else "04:00 PM"
        dur_fmt = parsed[2] if parsed else "8h 00m"
        dec_hrs = parsed[3] if parsed else 8.0
        state["status"] = "on"
        state["first_reply_at"] = now_str
        save_tracker_state(state)
        print(f"[Shift Tracker] 🌟 Omar replied ({now_str} -> {log_out_str})! Marking today as ON duty.", flush=True)
        sync_shift_to_vault(
            vault_dir=vault_dir,
            status="ON",
            check_in_time=now_str,
            check_out_time=log_out_str,
            dur_fmt=dur_fmt,
            dec_hrs=dec_hrs
        )
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


def sync_shift_to_vault(
    vault_dir=VAULT_DIR,
    status="ON",
    check_in_time=None,
    check_out_time="04:00 PM",
    dur_fmt="8h 00m",
    dec_hrs=8.0
):
    """
    Updates raw/attendance_october_2026.csv and omar_elnemr_attendance_october_2026.xlsx in the Obsidian vault.
    """
    now = get_damascus_now()
    date_str = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A")

    if not os.path.exists(vault_dir):
        return

    sync_excel_file(vault_dir, date_str, weekday, status, check_in_time, check_out_time, dur_fmt, dec_hrs)


def sync_excel_file(
    vault_dir,
    date_str,
    weekday,
    status,
    check_in_time,
    check_out_time="04:00 PM",
    dur_fmt="8h 00m",
    dec_hrs=8.0
):
    """Appends or updates the record in omar_elnemr_attendance_october_2026.xlsx with standardized styling."""
    candidates = [
        os.path.join(vault_dir, "omar_elnemr_attendance_october_2026.xlsx"),
        os.path.join(vault_dir, "omar_elnemr_attendance_2026.xlsx"),
        os.path.join(SCRIPT_DIR, "omar_elnemr_attendance_october_2026.xlsx"),
    ]
    target_paths = [p for p in candidates if os.path.exists(p)]
    if not target_paths:
        return

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        thin_side = Side(style="thin", color="00D9D9D9")
        border_data = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
        font_body = Font(name="Segoe UI", size=10, bold=False)
        zebra_fill = PatternFill(start_color="00F2F4F7", end_color="00F2F4F7", fill_type="solid")
        align_map = {
            1: Alignment(horizontal="center", vertical="center"),
            2: Alignment(horizontal="center", vertical="center"),
            3: Alignment(horizontal="left", vertical="center"),
            4: Alignment(horizontal="center", vertical="center"),
            5: Alignment(horizontal="center", vertical="center"),
            6: Alignment(horizontal="center", vertical="center"),
            7: Alignment(horizontal="center", vertical="center"),
            8: Alignment(horizontal="center", vertical="center"),
            9: Alignment(horizontal="left", vertical="center"),
        }

        log_in = check_in_time or ("08:00 AM" if status == "ON" else "OFF")
        log_out = check_out_time if status == "ON" else "OFF"
        d_fmt = dur_fmt if status == "ON" else "0h 00m"
        d_hrs = float(dec_hrs) if status == "ON" else 0.0
        stat_lbl = "Official Duty" if status == "ON" else "Day Off"
        notes = (
            f"✈️ Live Airport Ground Station Duty (Logged in {log_in} / Logged out {log_out})"
            if status == "ON"
            else "🌴 Scheduled Rest Day (No reply recorded)"
        )
        new_vals = [date_str, weekday, "Omar Elnemr", log_in, log_out, d_fmt, d_hrs, stat_lbl, notes]

        for excel_path in target_paths:
            wb = openpyxl.load_workbook(excel_path)
            ws = wb["Detailed Timesheet"] if "Detailed Timesheet" in wb.sheetnames else wb.active

            target_row = None
            total_row = None
            for r_idx in range(2, ws.max_row + 1):
                c1 = str(ws.cell(r_idx, 1).value or "")
                if c1 == date_str:
                    target_row = r_idx
                    break
                if c1.lower().startswith("total"):
                    total_row = r_idx

            if target_row is None:
                if total_row is not None:
                    ws.insert_rows(total_row, 1)
                    target_row = total_row
                else:
                    target_row = ws.max_row + 1

            is_even = (target_row % 2 == 0)
            for col_idx, val in enumerate(new_vals, start=1):
                c = ws.cell(target_row, col_idx, value=val)
                c.font = font_body
                if is_even:
                    c.fill = zebra_fill
                c.alignment = align_map[col_idx]
                c.border = border_data
                if col_idx == 7:
                    c.number_format = "0.00"
            ws.row_dimensions[target_row].height = 20

            wb.save(excel_path)
            print(f"[Shift Tracker] Synced {date_str} to standardized Excel sheet: {excel_path}", flush=True)
    except Exception as e:
        print(f"⚠ Failed to sync Excel sheet: {e}", flush=True)


def find_latest_excel_sheet(vault_dir=VAULT_DIR) -> str:
    """Finds the primary standardized October attendance Excel sheet in the vault or current repo."""
    candidates = [
        os.path.join(vault_dir, "omar_elnemr_attendance_october_2026.xlsx"),
        os.path.join(SCRIPT_DIR, "omar_elnemr_attendance_october_2026.xlsx"),
        os.path.join(vault_dir, "omar_elnemr_attendance_2026.xlsx"),
        os.path.join(vault_dir, "attendance_september_2026_standardized.xlsx"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    if os.path.exists(vault_dir):
        for root, dirs, files in os.walk(vault_dir):
            for f in files:
                if f.endswith(".xlsx"):
                    return os.path.join(root, f)
    return None


def send_excel_to_telegram(chat_id=TELEGRAM_USER_ID, token=TELEGRAM_BOT_TOKEN, vault_dir=VAULT_DIR, caption=None) -> bool:
    """Delivers the standardized October Excel sheet directly to Omar's Telegram chat via sendDocument."""
    file_path = find_latest_excel_sheet(vault_dir)
    if not file_path or not os.path.exists(file_path):
        print("⚠ No Excel sheet found to send.", flush=True)
        return False

    boundary = uuid.uuid4().hex
    filename = os.path.basename(file_path)
    cap = caption or "📊 Aethelgard Master Attendance & Shift Roster — October 2026 (Standardized Table Style)"

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
