#!/usr/bin/env python3
"""
🚨 Aethelgard Proactive VIP Inbox Triage & Vault Auto-Ingest Engine (R3)

Monitors Gmail in the background for high-priority/VIP communications:
  • Etihad Airways (@etihad.com, @etihad.ae, @etihadairways.com)
  • University of the People (@uopeople.edu)
  • Syrian Virtual University (@svuonline.org)
  • Aviation & Operations (@chamwings.com, @iata.org, work/training updates)

Automatically catalogs VIP emails into `raw/emails/` in the Aethelgard Obsidian Vault,
updates `index.md` via SecondBrainLibrarian, and dispatches instant Telegram push alerts.
"""

from __future__ import annotations

import os
import re
import sys
import json
import datetime
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

VIP_RULES = [
    {
        "domains": ("etihad.com", "etihad.ae", "etihadairways.com"),
        "keywords": ("etihad airways", "etihad"),
        "organization": "Etihad Airways",
        "category": "Aviation & Flight Operations",
        "priority": "CRITICAL",
    },
    {
        "domains": ("uopeople.edu",),
        "keywords": ("university of the people", "uopeople"),
        "organization": "University of the People",
        "category": "Academic / Computer Science",
        "priority": "HIGH",
    },
    {
        "domains": ("svuonline.org", "svu.edu.sy"),
        "keywords": ("syrian virtual university", "svuonline"),
        "organization": "Syrian Virtual University (SVU)",
        "category": "Academic / University",
        "priority": "HIGH",
    },
    {
        "domains": ("chamwings.com", "iata.org", "icao.int"),
        "keywords": ("cham wings", "iata training", "damascus airport"),
        "organization": "Aviation & Training Operations",
        "category": "Aviation & Work",
        "priority": "HIGH",
    },
]


def get_default_state_file() -> Path:
    base = os.environ.get("AETHELGARD_HOME", os.path.expanduser("~/.aethelgard"))
    state_dir = Path(base) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "gmail_seen_ids.json"


def classify_vip_sender(sender: str, subject: str = "") -> Dict[str, Any]:
    """
    Classifies a sender address (and optional subject) against the VIP taxonomy.
    """
    raw = (sender or "").strip().lower()
    subj_low = (subject or "").strip().lower()

    # Extract email domain if present
    email_match = re.search(r"[\w.\-+]+@([\w.\-]+)", raw)
    domain = email_match.group(1).lower() if email_match else raw

    for rule in VIP_RULES:
        for vip_dom in rule["domains"]:
            if domain == vip_dom or domain.endswith("." + vip_dom) or vip_dom in raw:
                return {
                    "is_vip": True,
                    "organization": rule["organization"],
                    "category": rule["category"],
                    "priority": rule["priority"],
                }
        for kw in rule["keywords"]:
            if kw in raw or kw in subj_low:
                return {
                    "is_vip": True,
                    "organization": rule["organization"],
                    "category": rule["category"],
                    "priority": rule["priority"],
                }

    # Check custom VIP domains/senders from environment
    extra_vips = [
        x.strip().lower()
        for x in os.environ.get("AETHELGARD_VIP_SENDERS", "").split(",")
        if x.strip()
    ]
    for ev in extra_vips:
        if ev in raw:
            return {
                "is_vip": True,
                "organization": ev.upper(),
                "category": "Custom VIP",
                "priority": "HIGH",
            }

    return {
        "is_vip": False,
        "organization": "General Sender",
        "category": "General",
        "priority": "NORMAL",
    }


def is_vip_sender(sender: str) -> bool:
    """Returns True if the sender matches any configured VIP domain or rule."""
    return bool(classify_vip_sender(sender).get("is_vip", False))


def _slugify(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "email").lower()).strip("-")
    return (s[:max_len].rstrip("-") or "vip-email")


def format_vip_alert(email_data: Dict[str, Any]) -> str:
    """
    Formats a high-priority Telegram push alert card for an incoming VIP email.
    """
    sender = email_data.get("sender", "unknown@example.com")
    subject = email_data.get("subject") or "(No Subject)"
    body = email_data.get("body") or email_data.get("snippet") or ""
    received_at = email_data.get("received_at") or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    classification = classify_vip_sender(sender, subject)
    org = classification.get("organization", "VIP Sender")
    priority = classification.get("priority", "HIGH")
    category = classification.get("category", "VIP")

    summary_lines = [ln.strip() for ln in str(body).splitlines() if ln.strip()]
    summary = " ".join(summary_lines[:3])[:350] if summary_lines else "(No body content)"

    return (
        f"🚨 *VIP EMAIL DETECTED: {org}*\n"
        f"• *Priority:* `{priority}` ({category})\n"
        f"• *From:* `{sender}`\n"
        f"• *Subject:* *{subject}*\n"
        f"• *Received:* `{received_at}`\n\n"
        f"📋 *Executive Summary:*\n"
        f"{summary}\n\n"
        f"📁 _Automatically cataloged into Aethelgard Vault (`raw/emails/`)._"
    )


def send_vip_telegram_alert(
    email_data: Dict[str, Any],
    chat_id: Optional[str] = None,
    token: Optional[str] = None,
) -> bool:
    """Dispatches an instant Telegram push alert for a VIP email."""
    bot_token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    target_chat = chat_id or os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")
    if not bot_token or not target_chat:
        return False

    alert_text = format_vip_alert(email_data)
    payload = {
        "chat_id": target_chat,
        "text": alert_text,
        "parse_mode": "Markdown",
        "reply_markup": {
            "keyboard": [
                ["📧 Draft Reply", "📂 Open Vault Note"],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        },
    }
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False


def ingest_vip_email_to_vault(
    email_data: Dict[str, Any],
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Catalogs a VIP email into `<vault_dir>/raw/emails/YYYY-MM-DD-<slug>.md` with
    structured YAML frontmatter and triggers SecondBrainLibrarian indexing.
    """
    target_vault = vault_dir or os.environ.get("VAULT_DIR", os.path.expanduser("~/vault"))
    vault_path = Path(os.path.expanduser(target_vault))
    emails_dir = vault_path / "raw" / "emails"
    emails_dir.mkdir(parents=True, exist_ok=True)

    msg_id = str(email_data.get("id") or "msg_unknown")
    sender = str(email_data.get("sender") or "unknown@example.com")
    subject = str(email_data.get("subject") or "VIP Email Notification")
    body = str(email_data.get("body") or email_data.get("snippet") or "")
    received_at = str(email_data.get("received_at") or datetime.datetime.now(datetime.timezone.utc).isoformat())

    classification = classify_vip_sender(sender, subject)
    org = classification.get("organization", "VIP")
    category = classification.get("category", "VIP")
    priority = classification.get("priority", "HIGH")

    date_prefix = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(f"{org}-{subject}")
    note_filename = f"{date_prefix}-{slug}.md"
    note_path = emails_dir / note_filename

    safe_subject = subject.replace('"', '\\"')
    safe_sender = sender.replace('"', '\\"')
    safe_org = org.replace('"', '\\"')

    markdown_content = (
        f"---\n"
        f'title: "{safe_subject}"\n'
        f'email_id: "{msg_id}"\n'
        f'sender: "{safe_sender}"\n'
        f'organization: "{safe_org}"\n'
        f'category: "{category}"\n'
        f'priority: "{priority}"\n'
        f'received_at: "{received_at}"\n'
        f'type: "vip_email"\n'
        f'tags:\n'
        f"  - email\n"
        f"  - vip\n"
        f"  - {_slugify(org, 25)}\n"
        f"---\n\n"
        f"# 📧 {subject}\n\n"
        f"- **Organization:** [[{org}]]\n"
        f"- **Sender:** `{sender}`\n"
        f"- **Priority:** `{priority}` ({category})\n"
        f"- **Received:** `{received_at}`\n"
        f"- **Message ID:** `{msg_id}`\n\n"
        f"## 📋 Content\n\n"
        f"{body}\n"
    )

    note_path.write_text(markdown_content, encoding="utf-8")

    librarian_status = None
    try:
        from librarian import SecondBrainLibrarian
        librarian_status = SecondBrainLibrarian(str(vault_path)).sync_index_catalog()
    except Exception as le:
        librarian_status = f"Librarian notice: {le}"

    return {
        "success": True,
        "vault_path": str(note_path),
        "organization": org,
        "priority": priority,
        "librarian_status": librarian_status,
    }


def process_incoming_email(
    email_data: Dict[str, Any],
    state_file: Optional[str] = None,
    vault_dir: Optional[str] = None,
    send_alert: bool = False,
) -> Dict[str, Any]:
    """
    Processes an incoming email with strict message-ID deduplication.
    If the message ID has already been seen, returns {"processed": False, "skipped": True}.
    """
    if not isinstance(email_data, dict):
        return {"processed": False, "skipped": True, "reason": "invalid_payload"}

    msg_id = str(email_data.get("id") or "").strip()
    if not msg_id:
        return {"processed": False, "skipped": True, "reason": "missing_id"}

    sf_path = Path(state_file) if state_file else get_default_state_file()
    sf_path.parent.mkdir(parents=True, exist_ok=True)

    seen_ids: List[str] = []
    if sf_path.exists():
        try:
            loaded = json.loads(sf_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                seen_ids = [str(x) for x in loaded]
            elif isinstance(loaded, dict):
                seen_ids = [str(x) for x in loaded.get("seen_ids", [])]
        except Exception:
            seen_ids = []

    if msg_id in seen_ids:
        return {
            "processed": False,
            "skipped": True,
            "id": msg_id,
            "reason": "duplicate_id",
        }

    seen_ids.append(msg_id)
    # Keep state file bounded to most recent 5000 message IDs
    if len(seen_ids) > 5000:
        seen_ids = seen_ids[-5000:]
    sf_path.write_text(json.dumps({"seen_ids": seen_ids}, indent=2), encoding="utf-8")

    sender = str(email_data.get("sender") or "")
    subject = str(email_data.get("subject") or "(No Subject)")
    vip_info = classify_vip_sender(sender, subject)
    is_vip = vip_info.get("is_vip", False)

    vault_res = None
    alert_sent = False
    if is_vip:
        if vault_dir:
            vault_res = ingest_vip_email_to_vault(email_data, vault_dir=vault_dir)
        if send_alert:
            alert_sent = send_vip_telegram_alert(email_data)

    return {
        "processed": True,
        "skipped": False,
        "id": msg_id,
        "is_vip": is_vip,
        "organization": vip_info.get("organization"),
        "vault_result": vault_res,
        "alert_sent": alert_sent,
    }


def run_vip_triage_cycle(
    vault_dir: Optional[str] = None,
    state_file: Optional[str] = None,
    send_alerts: bool = True,
    max_results: int = 15,
) -> Dict[str, Any]:
    """
    Polls Gmail for unread messages, filters for VIP senders, ingests matches into
    the Obsidian Vault, and sends Telegram push alerts.
    """
    from omnichannel_bridge import GoogleAdapter

    adapter = GoogleAdapter()
    status = adapter.get_status()
    if status.get("status") != "connected":
        return {"checked": 0, "vip_ingested": 0, "status": "needs_auth"}

    messages = adapter.search_emails(query="is:unread", max_results=max_results)
    vip_count = 0
    checked_count = 0

    for msg_stub in messages:
        msg_id = msg_stub.get("id")
        if not msg_id:
            continue
        checked_count += 1
        full_email = adapter.read_email(msg_id)
        if not is_vip_sender(full_email.get("sender", "")):
            continue
        res = process_incoming_email(
            full_email,
            state_file=state_file,
            vault_dir=vault_dir or os.environ.get("VAULT_DIR", os.path.expanduser("~/vault")),
            send_alert=send_alerts,
        )
        if res.get("processed") and res.get("is_vip"):
            vip_count += 1

    return {
        "checked": checked_count,
        "vip_ingested": vip_count,
        "status": "ok",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Proactive VIP Inbox Triage & Vault Auto-Ingest")
    parser.add_argument("--poll", action="store_true", help="Run one background VIP triage cycle")
    parser.add_argument("--check-sender", metavar="EMAIL", help="Check if an email address is a VIP sender")
    parser.add_argument("--vault", default=None, help="Override Obsidian Vault path")
    args = parser.parse_args()

    if args.check_sender:
        info = classify_vip_sender(args.check_sender)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0

    if args.poll:
        res = run_vip_triage_cycle(vault_dir=args.vault)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
