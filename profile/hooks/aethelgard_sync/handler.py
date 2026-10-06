import os
import sys
import time
import json

def handle(event_type, context):
    try:
        # Determine script directory with robust fallbacks
        candidates = [
            os.environ.get("AETHELGARD_DIR"),
            os.path.expanduser("~/aethelgard-custodian"),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            r"C:\Users\omara\Desktop\aethelgard-custodian"
        ]
        script_dir = None
        for c in candidates:
            if c and os.path.exists(os.path.join(c, "shift_tracker.py")):
                script_dir = c
                break
        if not script_dir:
            script_dir = os.path.expanduser("~/aethelgard-custodian")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        import shift_tracker

        hb_path = "/tmp/hermes_turn_heartbeat.json" if os.name != "nt" else os.path.join(script_dir, "heartbeat.json")
        vault_dir = os.environ.get("VAULT_DIR", os.path.expanduser("~/vault"))
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
        allowed_user = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")

        if event_type == "agent:start":
            msg = str(context.get("message", "")).strip().lower()
            chat_id = str(context.get("chat_id") or allowed_user)

            # 1. Update heartbeat for watchdog
            try:
                with open(hb_path, "w", encoding="utf-8") as f:
                    json.dump({"active": True, "start_time": time.time(), "message": msg[:80]}, f)
            except Exception:
                pass

            # 2. Mark shift ON on user reply
            shift_tracker.on_user_interaction(vault_dir=vault_dir, message_text=msg)

            # 3. Direct Excel sheet delivery if requested
            excel_triggers = ("excel", "sheet", "timesheet", "اكسل", "أكسل", "ملف الاكسل", "جدول الدوام", "/excel")
            if any(k in msg for k in excel_triggers):
                print(f"[Hook] 📊 Detected Excel sheet request in message: '{msg[:40]}'. Delivering document to Telegram...", flush=True)
                shift_tracker.send_excel_to_telegram(chat_id=chat_id, token=token, vault_dir=vault_dir)

            # 4. Immediate restart command if requested
            if msg in ("/restart", "/reboot"):
                print("[Hook] 🔄 Emergency restart requested via Telegram message.", flush=True)
                trigger_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(script_dir, "restart.trigger")
                with open(trigger_path, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))

        elif event_type == "agent:end":
            # Clear heartbeat
            try:
                with open(hb_path, "w", encoding="utf-8") as f:
                    json.dump({"active": False, "end_time": time.time()}, f)
            except Exception:
                pass

    except Exception as e:
        print(f"⚠ [Aethelgard Hook Notice] {e}", flush=True)
