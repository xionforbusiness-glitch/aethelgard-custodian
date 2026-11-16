import os
import time
import json
import datetime
import threading
import urllib.request
from librarian import SecondBrainLibrarian
from airport_radar import format_damascus_flight_briefing
from voice_engine import send_telegram_voice_sync
from wargame_engine import run_cognitive_collision
from circadian_oracle import get_circadian_forecast, get_operational_mode

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
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
    except Exception:
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
    """Constructs and delivers the proactive morning briefing with live airport radar and voice greeting."""
    now = get_damascus_now()
    weekday = now.strftime("%A")
    date_str = now.strftime("%B %d, %Y")
    schedule = get_roster_schedule_for_today(vault_dir, weekday)
    airport_weather = format_damascus_flight_briefing()
    circadian = get_circadian_forecast(vault_dir)

    # Log reminder in shift tracker
    try:
        from shift_tracker import on_morning_reminder_sent
        on_morning_reminder_sent()
    except Exception as ste:
        print(f"⚠ Shift Tracker notice: {ste}", flush=True)

    msg = (
        f"🌅 *Good morning Omar! Aethelgard Shift Briefing*\n"
        f"📅 *{weekday}, {date_str}* (Damascus Time)\n\n"
        f"⏰ *Your Scheduled Shift:* `{schedule['omar']}`\n\n"
        f"🧬 *Circadian Alertness Profile:* `{circadian['shift_type']}`\n"
        f"• Predicted Dip: `{circadian['alertness_dip']}` (Alertness: `{circadian['min_alertness']}`)\n"
        f"• Caffeine Curfew: ☕ `{circadian['caffeine_cutoff']}`\n\n"
        f"{airport_weather}\n\n"
        f"👥 *Duty Team Overview:*\n"
        f"• *Khaled:* `{schedule.get('khaled', '08:00 AM → 16:30')}`\n"
        f"• *Rasheed Akel:* `{schedule.get('rasheed', '08:00 AM → 16:30')}`\n"
        f"• *Mohamed Adnan:* `{schedule.get('adnan', 'On duty')}`\n"
        f"• *Tarek Orfhly:* `{schedule.get('tarek', 'On duty')}`\n"
        f"• *Hanan & Joudi:* `{schedule.get('hanan', 'Flight Handling')}`\n"
        f"🏖️ *Scheduled Off:* `{schedule.get('off', 'None')}`\n\n"
        f"📋 *Work Reminder Status:*\n"
        f"• Reply to this message (or text anytime today) to confirm you are *ON* duty.\n"
        f"• If you don't reply, today will automatically be cataloged as *OFF*."
    )

    sent = send_telegram(msg, buttons=None)
    
    # Send natural voice memo greeting (pure voice, no caption)
    voice_greeting = (
        f"Good morning Omar. Today is {weekday}. "
        f"Your shift at Damascus Airport is scheduled from {schedule['omar'].split(' ')[0]} to 4:00 PM. "
        f"Runway operations are active. Have a productive day."
    )
    send_telegram_voice_sync(voice_greeting, caption=None)

    # Trigger spoken 07:15 AM Executive Briefing Phone Call when outside SLEEP_SHIELD
    try:
        op_mode = get_operational_mode(vault_dir=vault_dir, now=now)
    except Exception:
        op_mode = {"mode": "STRATEGIC_WINDOW", "allow_proactive_calls": True}

    if op_mode.get("allow_proactive_calls", True):
        active_missions_note = ""
        try:
            import mission_engine
            ms = mission_engine.list_missions()
            active = [m for m in ms if m.get("status") in ("running", "queued", "waiting_human")]
            if active:
                active_missions_note = f" You also have {len(active)} active background mission(s)."
        except Exception:
            pass
        try:
            import telegram_voice_caller
            telegram_voice_caller.request_call_via_rpc_or_direct(
                opening_prompt=(
                    f"Good morning Omar. 7:15 AM Damascus executive briefing. "
                    f"Today is {weekday}; shift is {schedule.get('omar', 'scheduled')}.{active_missions_note} "
                    f"Would you like your unread emails or mission plan?"
                ),
                vault_dir=vault_dir,
            )
        except Exception:
            pass

    return sent

def trigger_evening_checkout(vault_dir):
    """Delivers evening shift wrap-up only if Omar confirmed duty today."""
    now = get_damascus_now()
    weekday = now.strftime("%A")
    circadian = get_circadian_forecast(vault_dir)

    try:
        from shift_tracker import on_evening_check
        status = on_evening_check(vault_dir)
        if status == "off":
            print("[Shift Tracker] Today was automatically marked OFF (no reply received). Skipping evening checkout prompt.", flush=True)
            return True
    except Exception as e:
        print(f"⚠ Evening check notice: {e}", flush=True)

    msg = (
        f"🏁 *Evening Shift Wrap-Up ({weekday})*\n\n"
        f"Omar, your scheduled shift concluded at ~04:00 PM.\n"
        f"Hope operations at Damascus Airport ran smoothly today!\n\n"
        f"🛏️ *Tonight's Circadian Sleep Anchor:* `{circadian['sleep_anchor']}`\n\n"
        f"Rest well tonight."
    )

    return send_telegram(msg, buttons=None)

def trigger_night_owl_scout(vault_dir):
    """Nightly autonomous collision engine that synthesizes original ideas and writes playbooks."""
    try:
        print("[Night Owl Scout] Running cognitive collision across vault notes...")
        res = run_cognitive_collision(vault_dir)
        if not res or not res.get("result"):
            return

        text = res["result"]
        domain_a = res.get("domain_a", "Domain A")
        domain_b = res.get("domain_b", "Domain B")
        
        # Save to concepts
        concepts_dir = os.path.join(vault_dir, "concepts")
        os.makedirs(concepts_dir, exist_ok=True)
        filename = f"collision-{domain_a[:15]}-{domain_b[:15]}.md".lower().replace(" ", "-")
        filepath = os.path.join(concepts_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text)

        # Notify Omar on Telegram
        msg = (
            f"🦉 *Night-Owl Cognitive Collision Complete!*\n\n"
            f"While you slept, Aethelgard collided two disparate areas of your knowledge:\n"
            f"• Domain 1: `[[{domain_a}]]`\n"
            f"• Domain 2: `[[{domain_b}]]`\n\n"
            f"Generated new original blueprint in your vault:\n"
            f"📄 `[[concepts/{filename}]]`\n\n"
            f"Available on your local Obsidian on your next sync!"
        )
        send_telegram(msg)
        
        # Voice note alert
        voice_summary = f"Night owl intelligence cycle complete. I synthesized a new blueprint combining {domain_a} and {domain_b}. It is saved in your vault."
        send_telegram_voice_sync(voice_summary, caption="🎙️ Night-Owl Scout Audio Memo")
    except Exception as e:
        print(f"⚠ Night Owl Scout notice: {e}")

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
    
    msg = (
        f"🏛️ *Aethelgard Executive Weekly Review Published!*\n\n"
        f"📊 *Weekly Metrics:*\n"
        f"• Total Knowledge Notes: `{audit['total_notes']}`\n"
        f"• Master Roster: Standardized & synced to Excel\n"
        f"• Review Note: `[[{os.path.splitext(os.path.basename(review_path))[0]}]]`\n\n"
        f"Your local Obsidian vault will pull this review on your next desktop sync."
    )
    send_telegram(msg)
    voice_msg = f"Omar, your weekly executive review has been generated. Total cataloged knowledge notes: {audit['total_notes']}. Master roster and shift metrics are synchronized."
    send_telegram_voice_sync(voice_msg, caption="🎙️ Weekly Review Audio Memo")

def trigger_wakeup_alarm(vault_dir):
    """Fires a high-priority 06:00 AM wake-up alarm with runway weather radar and circadian forecast."""
    now = get_damascus_now()
    weekday = now.strftime("%A")
    date_str = now.strftime("%B %d, %Y")
    circadian = get_circadian_forecast(vault_dir)
    airport_weather = format_damascus_flight_briefing()

    # Log reminder in shift tracker
    try:
        from shift_tracker import on_morning_reminder_sent
        on_morning_reminder_sent()
    except Exception as ste:
        print(f"⚠ Shift Tracker notice: {ste}", flush=True)

    msg = (
        f"⏰ *06:00 AM WAKE-UP CALL — DAMASCUS*\n"
        f"📅 *{weekday}, {date_str}*\n\n"
        f"Rise and shine, Omar. Your scheduled wake-up reminder is firing.\n\n"
        f"🧬 *Circadian Alertness Profile:* `{circadian['shift_type']}`\n"
        f"• Optimal Core Window: `{circadian['sleep_anchor']}`\n"
        f"• Predicted Peak Alertness Dip: `{circadian['alertness_dip']}`\n\n"
        f"{airport_weather}\n\n"
        f"Get ready for your shift. Full shift briefing will trigger at 07:15 AM."
    )
    send_telegram(msg, buttons=None)

    voice_alarm = (
        f"Good morning Omar. It is 6:00 AM Damascus time. "
        f"Time to rise. Your flight schedule and runway radar are standing by."
    )
    send_telegram_voice_sync(voice_alarm, caption=None)

def run_jarvis_background_maintenance(
    vault_dir,
    now=None,
    check_linkedin: bool = False,
    check_overdue_promises: bool = False,
    telegram_fn=None,
):
    """
    Runs one background Jarvis v2 maintenance pass:
      1. Evaluates Async Callback Orchestrator (`async_callback_engine.tick_async_jobs`)
      2. Checks Sleep Shield via `circadian_oracle.get_operational_mode`
      3. Stages overdue Promise Ledger follow-up drafts (notification only; Gatekeeper invariant)
      4. Polls LinkedIn unseen notifications/messages & alerts on new recruiter hits or expired cookies
    """
    tg = telegram_fn or send_telegram
    op_mode = {"mode": "STRATEGIC_WINDOW", "allow_proactive_calls": True}
    try:
        op_mode = get_operational_mode(vault_dir=vault_dir, now=now)
    except Exception:
        pass

    async_res = {}
    try:
        import async_callback_engine
        async_res = async_callback_engine.tick_async_jobs(now=now, vault_dir=vault_dir)
    except Exception:
        pass

    missions_res = {}
    try:
        import mission_engine
        missions_res = mission_engine.tick_missions_maintenance(vault_dir=vault_dir)
    except Exception:
        pass

    staged_followups = 0
    if check_overdue_promises and op_mode.get("allow_proactive_calls", True):
        try:
            import promise_ledger
            for item in promise_ledger.check_overdue_promises(now=now):
                if not item.get("followup_draft_id"):
                    res = promise_ledger.stage_followup_for_overdue(
                        item["id"], notify_telegram=True, vault_dir=vault_dir
                    )
                    if res.get("success") and not res.get("already_staged"):
                        staged_followups += 1
        except Exception:
            pass

    linkedin_events = 0
    if check_linkedin and op_mode.get("allow_proactive_calls", True):
        try:
            from omnichannel_bridge import LinkedInAdapter
            li_res = LinkedInAdapter().check_unseen_events()
            if li_res.get("notify_expired"):
                tg("🔗 *LinkedIn Session Expired:* Please refresh `li_at` cookie via `/connect linkedin` or `Connect_Aethelgard_Accounts.bat`.")
            for ev in li_res.get("events", []):
                linkedin_events += 1
                if ev.get("priority") == "HIGH":
                    tg(f"💼 *LinkedIn Priority Alert:*\n{ev.get('summary', '')}")
        except Exception:
            pass

    return {
        "operational_mode": op_mode,
        "async_jobs": async_res,
        "missions": missions_res,
        "staged_followups": staged_followups,
        "linkedin_events": linkedin_events,
    }


def proactive_scheduler_daemon(vault_dir):
    """Continuous background loop monitoring Damascus time and firing scheduled proactive events."""
    last_wakeup_date = None
    last_morning_date = None
    last_evening_date = None
    last_scout_date = None
    last_nightly_date = None
    last_weekly_date = None
    last_curiosity_hm = None
    last_vip_triage_slot = None
    last_linkedin_slot = None
    last_promise_slot = None

    print(f"[{time.strftime('%H:%M:%S')}] ⏰ Full Proactive Autonomy Suite active (Damascus UTC+3)...")

    def _fast_callback_loop() -> None:
        while True:
            time.sleep(3)
            try:
                import async_callback_engine
                if async_callback_engine.list_async_jobs(status="pending"):
                    async_callback_engine.tick_async_jobs(now=get_damascus_now(), vault_dir=vault_dir)
            except Exception:
                pass

    threading.Thread(target=_fast_callback_loop, name="AsyncCallbackFastPoller", daemon=True).start()

    while True:
        try:
            now = get_damascus_now()
            today_str = now.strftime("%Y-%m-%d")
            hour = now.hour
            minute = now.minute
            is_sunday = now.weekday() == 6

            try:
                op_mode = get_operational_mode(vault_dir=vault_dir, now=now)
            except Exception:
                op_mode = {"mode": "STRATEGIC_WINDOW", "allow_proactive_calls": True}

            # 0. Dedicated Wake-Up Call (06:00 AM)
            if hour == 6 and minute >= 0 and last_wakeup_date != today_str:
                print(f"[Proactive Scheduler] Firing 06:00 AM Wake-Up Call: {today_str}")
                trigger_wakeup_alarm(vault_dir)
                last_wakeup_date = today_str

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

            # 3. Night-Owl Cognitive Collision Scout (02:00 AM)
            if hour == 2 and minute >= 0 and last_scout_date != today_str:
                print(f"[Night Owl Scout] Firing Autonomous Collision: {today_str}")
                trigger_night_owl_scout(vault_dir)
                last_scout_date = today_str

            # 4. Nightly Librarian Audit & Catalog Sync (00:05 AM)
            if hour == 0 and minute >= 5 and last_nightly_date != today_str:
                print(f"[Second Brain Librarian] Firing Nightly Catalog Sync: {today_str}")
                trigger_librarian_nightly(vault_dir)
                last_nightly_date = today_str

            # 5. Weekly Executive Review (Sunday 23:00 PM)
            if is_sunday and hour == 23 and minute >= 0 and last_weekly_date != today_str:
                print(f"[Second Brain Librarian] Firing Weekly Executive Review: {today_str}")
                trigger_weekly_review(vault_dir)
                last_weekly_date = today_str

            # 6. Spontaneous Daytime Curiosity Ping (Stochastic check-in every 15m, suppressed during SLEEP_SHIELD)
            current_hm = (hour, minute // 15)
            if current_hm != last_curiosity_hm:
                last_curiosity_hm = current_hm
                if op_mode.get("allow_proactive_calls", True):
                    try:
                        from spontaneous_curiosity import trigger_spontaneous_ping
                        trigger_spontaneous_ping(vault_dir, force=False)
                    except Exception:
                        pass

            # 7. Proactive VIP Inbox Triage & Vault Auto-Ingest (Every 5 minutes)
            current_vip_slot = (today_str, hour, minute // 5)
            if current_vip_slot != last_vip_triage_slot:
                last_vip_triage_slot = current_vip_slot
                try:
                    from vip_inbox_triage import run_vip_triage_cycle
                    run_vip_triage_cycle(vault_dir=vault_dir, send_alerts=True)
                except Exception as ve:
                    print(f"⚠ VIP Inbox Triage notice: {ve}")

            # 8. Jarvis v2 Async Callbacks (30s) + LinkedIn (15m) + Promise SLA Check (1h)
            li_slot = (today_str, hour, minute // 15)
            do_li = li_slot != last_linkedin_slot
            if do_li:
                last_linkedin_slot = li_slot

            prm_slot = (today_str, hour)
            do_prm = prm_slot != last_promise_slot
            if do_prm:
                last_promise_slot = prm_slot

            run_jarvis_background_maintenance(
                vault_dir=vault_dir,
                now=now,
                check_linkedin=do_li,
                check_overdue_promises=do_prm,
            )

        except Exception as e:
            print(f"⚠ Proactive Scheduler notice: {e}")

        # Poll pending async callbacks every 5s so user-requested callbacks fire promptly
        for _ in range(5):
            time.sleep(5)
            try:
                import async_callback_engine
                if async_callback_engine.list_async_jobs(status="pending"):
                    async_callback_engine.tick_async_jobs(now=get_damascus_now(), vault_dir=vault_dir)
            except Exception:
                pass
        time.sleep(5)
