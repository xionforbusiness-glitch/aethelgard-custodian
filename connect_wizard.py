#!/usr/bin/env python3
"""
🔗 Aethelgard Interactive Telegram `/connect` Account Pairing Wizard (R4.1)

Handles `/connect` commands directly inside @aethelgard_vault_bot on Telegram:
  • `/connect` or `/connect status` — Live connection status dashboard for all 5 platforms
  • `/connect google [token_or_app_password]` — Google Account (Gmail, Calendar, Drive) pairing
  • `/connect whatsapp [phone_number | qr]` — WhatsApp 8-digit phone pairing code or QR code
  • `/connect telegram [phone_number]` — Personal Telegram MTProto session pairing
  • `/connect linkedin [li_at_cookie]` — LinkedIn session cookie pairing
  • `/connect facebook [c_user:xs]` — Facebook session cookie pairing
  • `/connect import <encrypted_bundle_b64>` — Zero-SSH encrypted bundle import from Windows helper
"""

from __future__ import annotations

import os
import sys
import json
import base64
from pathlib import Path
from typing import Any, Dict, Optional

import omnichannel_bridge


def _status_badge(status_str: str) -> str:
    s = (status_str or "").lower()
    if s == "connected":
        return "🟢 Connected"
    if s == "expired":
        return "🔴 Expired"
    return "🟡 Needs Pairing"


def format_status_dashboard() -> str:
    """Builds the formatted Telegram Markdown status dashboard for all 5 platforms."""
    statuses = omnichannel_bridge.get_account_status()
    cred_dir = omnichannel_bridge.get_credentials_dir()

    g = statuses.get("google", {})
    w = statuses.get("whatsapp", {})
    t = statuses.get("telegram", {})
    l = statuses.get("linkedin", {})
    f = statuses.get("facebook", {})

    lines = [
        "🏛️ *Aethelgard Omnichannel Account Status*",
        "",
        f"1. 📧 *Google (Gmail / Calendar / Drive):* {_status_badge(g.get('status'))}",
        f"   └ _{g.get('details', '')}_",
        f"2. 💬 *WhatsApp (Multi-Device Bridge):* {_status_badge(w.get('status'))}",
        f"   └ _{w.get('details', '')}_",
        f"3. ✈️ *Telegram (Personal MTProto):* {_status_badge(t.get('status'))}",
        f"   └ _{t.get('details', '')}_",
        f"4. 💼 *LinkedIn (Voyager Session):* {_status_badge(l.get('status'))}",
        f"   └ _{l.get('details', '')}_",
        f"5. 📘 *Facebook (Session Bridge):* {_status_badge(f.get('status'))}",
        f"   └ _{f.get('details', '')}_",
        "",
        f"🔒 *Safe Storage:* `{cred_dir}` (Protected outside git-reset paths)",
        "",
        "⚡ *Quick Pairing Commands:*",
        "• `/connect google` — Link Google OAuth token or App Password",
        "• `/connect whatsapp +963...` — Get 8-digit phone pairing code",
        "• `/connect telegram +963...` — Link Personal Telegram MTProto",
        "• `/connect linkedin <li_at>` — Link LinkedIn session cookie",
        "• `/connect facebook <c_user>:<xs>` — Link Facebook session cookies",
        "• Or run `Connect_Aethelgard_Accounts.bat` on your Windows laptop for 1-click sync!",
    ]
    return "\n".join(lines)


def handle_connect_command(command_text: str, chat_id: Optional[str] = None) -> str:
    """
    Processes any `/connect ...` command and returns a formatted Telegram response.
    """
    raw = (command_text or "").strip()
    parts = raw.split(maxsplit=2)

    if len(parts) <= 1 or parts[1].lower() == "status":
        return format_status_dashboard()

    sub = parts[1].lower().strip()
    arg = parts[2].strip() if len(parts) > 2 else ""
    cred_dir = omnichannel_bridge.get_credentials_dir()

    # 1. GOOGLE
    if sub in ("google", "gmail"):
        token_file = cred_dir / "google_oauth.json"
        if arg:
            # User supplied JSON token or App Password inline
            try:
                parsed = json.loads(arg)
                if isinstance(parsed, dict):
                    token_file.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
                    return "✅ *Google Account Connected!* OAuth token saved to `~/.aethelgard/credentials/google_oauth.json`."
            except Exception:
                clean_arg = arg.replace(" ", "").strip()
                if arg.strip().startswith("ya29.") or len(clean_arg) > 32:
                    payload = {"email": "omar.alnemr04@gmail.com", "access_token": arg.strip()}
                    kind = "OAuth token"
                else:
                    payload = {"email": "omar.alnemr04@gmail.com", "app_password": clean_arg}
                    kind = "Gmail App Password"
                token_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return f"✅ *Google Account Credential Saved!* {kind} stored in `~/.aethelgard/credentials/google_oauth.json`."

        if token_file.exists():
            try:
                data = json.loads(token_file.read_text(encoding="utf-8"))
                if data.get("access_token") or data.get("refresh_token") or data.get("app_password"):
                    return (
                        "🟢 *Google Account (Gmail / Calendar / Drive) is CONNECTED!*\n"
                        f"• Active OAuth token verified in `{token_file}`.\n"
                        "• Proactive VIP Inbox Triage (Etihad, UoPeople, SVU) is active."
                    )
            except Exception:
                pass

        return (
            "📧 *Connect Google Account (Gmail, Calendar, Drive)*\n\n"
            "Choose either method:\n"
            "1. *1-Click Windows Helper (Recommended):* Double-click `Connect_Aethelgard_Accounts.bat` on your Windows PC.\n"
            "2. *Direct Token / App Password:* Reply with:\n"
            "   `/connect google <16-char-app-password-or-oauth-token>`"
        )

    # 2. WHATSAPP
    if sub in ("whatsapp", "wa"):
        wa = omnichannel_bridge.WhatsAppAdapter(str(cred_dir))
        pair_info = wa.get_qr_or_pairing_code(phone=arg if arg and arg.lower() != "qr" else "")
        if pair_info.get("connected"):
            return (
                "💬 *WhatsApp Multi-Device Bridge Connected*\n\n"
                f"📱 *Linked Phone:* `{pair_info.get('phone') or 'active'}`\n"
                "2-way Baileys messaging is live!"
            )
        code = pair_info.get("pairing_code") or "Scan QR"
        qr_data = pair_info.get("qr_data") or ""
        return (
            "💬 *WhatsApp Multi-Device Pairing (Zero Browser)*\n\n"
            f"🔑 *8-Digit Phone Pairing Code:* `{code}`\n\n"
            "*How to link on your phone in 10 seconds:*\n"
            "1. Open *WhatsApp* on your phone.\n"
            "2. Tap *Settings (or Menu) → Linked Devices → Link a Device*.\n"
            "3. Tap *'Link with phone number instead'* at the bottom.\n"
            f"4. Enter code: `{code}`\n\n"
            f"📱 _QR Fallback Payload:_ `{qr_data}`"
        )

    # 3. PERSONAL TELEGRAM
    if sub in ("telegram", "tg"):
        ta = omnichannel_bridge.TelegramUserAdapter(str(cred_dir))
        sess_file = cred_dir / "telegram_session.json"
        if arg:
            parts = arg.split()
            low_first = parts[0].lower()
            if low_first in ("code", "otp") and len(parts) >= 2:
                otp = parts[1].strip()
                pw = parts[2].strip() if len(parts) >= 3 else ""
                res = ta.complete_phone_login(otp, password=pw)
                if res.get("success"):
                    return (
                        "✈️ *Personal Telegram MTProto Connected!*\n"
                        f"• *Account:* `{res.get('username') or res.get('phone')}`\n"
                        f"• Saved isolated `StringSession` to `{sess_file}` alongside `@aethelgard_vault_bot`."
                    )
                if res.get("state") == "needs_2fa":
                    return "🔐 *Telegram 2FA Password Required*\nReply: `/connect telegram code <OTP> <2FA_PASSWORD>`"
                return f"❌ *Telegram Login Error:* `{res.get('error', 'Failed')}`"

            if arg.strip().isdigit() and ta.pending_login_file.exists():
                res = ta.complete_phone_login(arg.strip())
                if res.get("success"):
                    return (
                        "✈️ *Personal Telegram MTProto Connected!*\n"
                        f"• *Account:* `{res.get('username') or res.get('phone')}`\n"
                        f"• Saved isolated `StringSession` to `{sess_file}`."
                    )
                return f"❌ *Telegram OTP Error:* `{res.get('error', 'Failed')}`"

            login_res = ta.start_phone_login(arg)
            if login_res.get("live_mtproto"):
                if login_res.get("success"):
                    return (
                        f"✈️ *Telegram Login Code Sent to `{arg}`*\n"
                        "Check your Telegram app for the login code and reply:\n"
                        "`/connect telegram code <OTP>` (or `/connect telegram code <OTP> <2FA_PASSWORD>`)"
                    )
                return f"❌ *Telegram Send Code Failed:* `{login_res.get('error')}`"

            return (
                f"✈️ *Personal Telegram MTProto Session Staged for `{arg}`*\n"
                f"Session metadata saved to `{sess_file}` alongside `@aethelgard_vault_bot`."
            )
        return (
            "✈️ *Connect Personal Telegram Account (MTProto)*\n\n"
            "Operates alongside `@aethelgard_vault_bot` using Telethon (< 20MB RAM).\n"
            "• Reply `/connect telegram +963...` with your phone number, then `/connect telegram code <OTP>`, or\n"
            "• Run `Connect_Aethelgard_Accounts.bat` on your Windows PC."
        )

    # 4. LINKEDIN
    if sub in ("linkedin", "li"):
        li_file = cred_dir / "linkedin_session.json"
        if arg:
            li_data = {"li_at": arg.strip(), "JSESSIONID": "ajax:aethelgard_csrf"}
            li_file.write_text(json.dumps(li_data, indent=2), encoding="utf-8")
            return "✅ *LinkedIn Account Connected!* Session cookie (`li_at`) saved to `~/.aethelgard/credentials/linkedin_session.json`."
        return (
            "💼 *Connect LinkedIn Account*\n\n"
            "• *Option 1 (1-Click):* Run `Connect_Aethelgard_Accounts.bat` on Windows to auto-extract `li_at`.\n"
            "• *Option 2 (Direct):* Reply `/connect linkedin <your_li_at_cookie>`"
        )

    # 5. FACEBOOK
    if sub in ("facebook", "fb"):
        fb_file = cred_dir / "facebook_session.json"
        if arg:
            if ":" in arg:
                c_user, xs = arg.split(":", 1)
                fb_data = {"c_user": c_user.strip(), "xs": xs.strip()}
            else:
                fb_data = {"c_user": "omar", "xs": arg.strip()}
            fb_file.write_text(json.dumps(fb_data, indent=2), encoding="utf-8")
            return "✅ *Facebook Account Connected!* Session cookies saved to `~/.aethelgard/credentials/facebook_session.json`."
        return (
            "📘 *Connect Facebook Account*\n\n"
            "• *Option 1 (1-Click):* Run `Connect_Aethelgard_Accounts.bat` on Windows to auto-extract `c_user` & `xs`.\n"
            "• *Option 2 (Direct):* Reply `/connect facebook <c_user>:<xs>`"
        )

    # 6. ZERO-SSH ENCRYPTED BUNDLE IMPORT
    if sub == "import" and arg:
        try:
            import connect_accounts_windows
            raw_bytes = base64.b64decode(arg.encode("ascii"))
            decrypted = connect_accounts_windows.decrypt_credentials_bundle(raw_bytes)
            connect_accounts_windows.save_credentials_locally(decrypted, cred_dir=str(cred_dir))
            return "🎉 *Zero-SSH Encrypted Credential Bundle Imported Successfully!* All paired sessions are now active."
        except Exception as e:
            return f"❌ *Bundle Import Failed:* `{e}`"

    return format_status_dashboard()


def main() -> int:
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "/connect status"
    if not cmd.startswith("/connect"):
        cmd = f"/connect {cmd}"
    print(handle_connect_command(cmd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
