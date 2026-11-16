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

        # Apply typing indicator lifecycle patches on startup if available
        try:
            import gateway_typing_patch
            gateway_typing_patch.apply_typing_lifecycle_patches()
        except Exception:
            pass

        import shift_tracker

        vault_dir = os.environ.get("VAULT_DIR", os.path.expanduser("~/vault"))
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        allowed_user = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")

        if event_type == "agent:start":
            raw_msg = str(context.get("message", "")).strip()
            msg = raw_msg.lower()
            chat_id = str(context.get("chat_id") or allowed_user)

            # 1. Mark shift ON on user reply
            shift_tracker.on_user_interaction(vault_dir=vault_dir, message_text=msg)

            # 2. Direct Excel sheet delivery ONLY if explicitly requested via specific command
            explicit_excel_commands = {
                "/excel", "/timesheet", "/sheet",
                "send excel", "send the excel", "send spreadsheet", "send the sheet",
                "ارسل الاكسل", "ابعت الاكسل", "ارسل ملف الاكسل", "ابعت ملف الاكسل"
            }
            if msg in explicit_excel_commands:
                print(f"[Hook] 📊 Explicit Excel request '{msg}'. Delivering document to Telegram...", flush=True)
                shift_tracker.send_excel_to_telegram(chat_id=chat_id, token=token, vault_dir=vault_dir)

            # 2b. Remote Supervisor Service /restart & /reboot Interception
            if msg in ("/restart", "/reboot"):
                try:
                    restart_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(script_dir, "restart.trigger")
                    with open(restart_path, "w", encoding="utf-8") as rf:
                        rf.write("manual_restart_requested")
                    from proactive_scheduler import send_telegram
                    send_telegram("🔄 *Restarting Aethelgard Custodian...*\nPulling latest code, clearing stale sessions, and reloading 24/7 background service in 3 seconds.", chat_id=chat_id, token=token)
                except Exception as r_err:
                    print(f"⚠ [Hook /restart Notice] {r_err}", flush=True)

            # 2c. Session Reset Guide Interception
            if msg in ("/clear", "/wipe"):
                try:
                    from proactive_scheduler import send_telegram
                    send_telegram("💡 *Session Reset:* Send `/new` or `/reset` directly in chat to clear all conversational context and start with zero token overhead.", chat_id=chat_id, token=token)
                except Exception:
                    pass

            # 3. Omnichannel /connect Wizard Interception
            if msg == "/connect" or msg.startswith("/connect "):
                try:
                    import connect_wizard
                    from proactive_scheduler import send_telegram
                    reply_text = connect_wizard.handle_connect_command(raw_msg, chat_id=chat_id)
                    send_telegram(reply_text, chat_id=chat_id, token=token)
                except Exception as ce:
                    print(f"⚠ [Hook /connect Notice] {ce}", flush=True)

            # 3b. Mission Control (/mission & /resume) Interception
            if msg == "/mission" or msg.startswith("/mission ") or msg == "/resume" or msg.startswith("/resume "):
                try:
                    import mission_engine
                    from proactive_scheduler import send_telegram
                    reply_text = mission_engine.handle_mission_command(raw_msg, vault_dir=vault_dir)
                    send_telegram(reply_text, chat_id=chat_id, token=token)
                except Exception as me:
                    print(f"⚠ [Hook /mission Notice] {me}", flush=True)

            # 4. Strict Two-Phase Outbound Approval Gatekeeper Interception
            try:
                import approval_gatekeeper
                approval_res = approval_gatekeeper.handle_approval_reply(raw_msg)
                if approval_res is not None:
                    from proactive_scheduler import send_telegram
                    d_id = approval_res.get("draft_id", "")
                    st = approval_res.get("status", "")
                    plat = str(approval_res.get("platform", "")).upper()
                    if st == "approved" and approval_res.get("success"):
                        send_telegram(
                            f"✅ *Authorized & Dispatched!*\n• *Draft ID:* `{d_id}`\n• *Platform:* `{plat}`",
                            chat_id=chat_id,
                            token=token,
                        )
                    elif st == "rejected":
                        send_telegram(
                            f"❌ *Draft Discarded*\n• *Draft ID:* `{d_id}`\n• *Platform:* `{plat}`\n• Zero network packets transmitted.",
                            chat_id=chat_id,
                            token=token,
                        )
            except Exception as ae:
                print(f"⚠ [Hook Approval Gatekeeper Notice] {ae}", flush=True)

            # 5. Immediate restart command if requested
            if msg in ("/restart", "/reboot"):
                print("[Hook] 🔄 Emergency restart requested via Telegram message.", flush=True)
                trigger_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(script_dir, "restart.trigger")
                with open(trigger_path, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))

    except Exception as e:
        print(f"⚠ [Aethelgard Hook Notice] {e}", flush=True)
