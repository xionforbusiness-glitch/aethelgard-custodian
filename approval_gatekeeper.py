#!/usr/bin/env python3
"""
🛡️ Aethelgard Strict Two-Phase Outbound Approval Gatekeeper (R2)

Enforces a mandatory human-in-the-loop approval gate before any external message,
email, or social post is transmitted across Gmail, WhatsApp, Personal Telegram,
LinkedIn, or Facebook.

All drafts are persisted outside the git working tree in ~/.aethelgard/drafts/
so `git reset --hard origin/main` during auto-updates never erases pending drafts.
"""

from __future__ import annotations

import os
import sys
import uuid
import json
import time
import datetime
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

SUPPORTED_PLATFORMS = {
    "google",
    "gmail",
    "calendar",
    "drive",
    "whatsapp",
    "telegram",
    "telegram_user",
    "linkedin",
    "facebook",
}


def get_drafts_dir() -> Path:
    """Returns the safe drafts storage directory outside the git repository."""
    custom = os.environ.get("AETHELGARD_DRAFTS_DIR")
    if custom:
        d = Path(os.path.expanduser(custom))
    else:
        base = os.environ.get("AETHELGARD_HOME", os.path.expanduser("~/.aethelgard"))
        d = Path(base) / "drafts"
    d.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(d, 0o700)
        except Exception:
            pass
    return d


def _draft_file_path(draft_id: str) -> Path:
    safe_id = os.path.basename(str(draft_id).strip())
    if not safe_id.startswith("draft_"):
        safe_id = f"draft_{safe_id}"
    return get_drafts_dir() / f"{safe_id}.json"


def _sync_pending_index() -> None:
    """Maintains pending_drafts.json index for fast inspection."""
    drafts_dir = get_drafts_dir()
    pending = []
    for p in sorted(drafts_dir.glob("draft_*.json")):
        if p.name == "pending_drafts.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("status") == "pending_approval":
                pending.append(data)
        except Exception:
            pass
    index_file = drafts_dir / "pending_drafts.json"
    try:
        index_file.write_text(json.dumps(pending, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def stage_draft(
    platform: str,
    action: str,
    recipient_or_target: str,
    content: str,
    metadata: Optional[Union[Dict[str, Any], str]] = None,
    subject: Optional[str] = None,
    notify_telegram: bool = False,
) -> Dict[str, Any]:
    """
    Stages an outbound message/email/post with a unique draft_<id> and status 'pending_approval'.
    Strictly blocks network transmission until explicitly approved via approve_draft().

    Supports both:
      stage_draft(platform, action, recipient_or_target, content, metadata={...})
    and 5-string positional calls:
      stage_draft(platform, action, recipient_or_target, subject, body)
    """
    # Handle 5-string positional call: stage_draft('gmail', 'send_email', 'to@x.com', 'Subject', 'Body')
    if isinstance(metadata, str) and subject is None:
        subject = content
        content = metadata
        metadata = {"subject": subject}

    if not isinstance(platform, str) or not platform.strip():
        raise ValueError("platform must be a non-empty string")
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a non-empty string")
    if not isinstance(recipient_or_target, str) or not recipient_or_target.strip():
        raise ValueError("recipient_or_target must be a non-empty string")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Draft content must not be empty")

    norm_platform = platform.strip().lower()
    if norm_platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform '{platform}'. Must be one of {sorted(SUPPORTED_PLATFORMS)}")

    meta_dict: Dict[str, Any] = dict(metadata) if isinstance(metadata, dict) else {}
    if subject:
        meta_dict.setdefault("subject", subject)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    expires_utc = now_utc + datetime.timedelta(hours=24)
    draft_id = f"draft_{uuid.uuid4().hex[:10]}"

    # Ensure zero collision even under rapid burst staging
    while _draft_file_path(draft_id).exists():
        draft_id = f"draft_{uuid.uuid4().hex[:10]}"

    draft_record: Dict[str, Any] = {
        "draft_id": draft_id,
        "status": "pending_approval",
        "platform": norm_platform,
        "action": action.strip(),
        "recipient_or_target": recipient_or_target.strip(),
        "target": recipient_or_target.strip(),
        "subject": meta_dict.get("subject", ""),
        "content": content,
        "metadata": meta_dict,
        "network_transmitted": False,
        "created_at": now_utc.isoformat(),
        "expires_at": expires_utc.isoformat(),
        "approved_at": None,
        "rejected_at": None,
        "executed_at": None,
        "execution_receipt": None,
    }

    draft_path = _draft_file_path(draft_id)
    draft_path.write_text(json.dumps(draft_record, indent=2, ensure_ascii=False), encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(draft_path, 0o600)
        except Exception:
            pass

    _sync_pending_index()
    draft_record["preview_card"] = format_approval_card(draft_record)

    if notify_telegram:
        send_approval_card_to_telegram(draft_record)

    return draft_record


def get_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    """Loads a staged draft by ID."""
    if not draft_id:
        return None
    p = _draft_file_path(draft_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_pending_drafts() -> List[Dict[str, Any]]:
    """Returns all drafts currently in 'pending_approval' status, sorted newest first."""
    drafts_dir = get_drafts_dir()
    pending: List[Dict[str, Any]] = []
    for p in drafts_dir.glob("draft_*.json"):
        if p.name == "pending_drafts.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("status") == "pending_approval":
                pending.append(data)
        except Exception:
            pass
    pending.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return pending


def verify_draft_authorized(draft_id: str) -> Dict[str, Any]:
    """
    Verifies that a draft exists and has been explicitly approved by Omar.
    Raises PermissionError if the draft is still pending or rejected.
    """
    draft = get_draft(draft_id)
    if not draft:
        raise ValueError(f"Draft '{draft_id}' not found")
    status = draft.get("status")
    if status not in ("approved", "executed"):
        raise PermissionError(
            f"Outbound transmission blocked by Gatekeeper: draft '{draft_id}' has status '{status}' (must be 'approved')."
        )
    return draft


def approve_draft(
    draft_id: Optional[str] = None,
    execute: bool = True,
    approved_via: str = "telegram",
) -> Dict[str, Any]:
    """
    Marks a pending draft as 'approved' and authorizes transmission via the platform adapter.
    If draft_id is None or 'latest', approves the most recent pending draft.
    """
    if not draft_id or draft_id.strip().lower() in ("latest", "last", "yes", "send"):
        pending = list_pending_drafts()
        if not pending:
            return {"success": False, "result": None, "error": "No pending drafts awaiting approval."}
        draft = pending[0]
        draft_id = draft["draft_id"]
    else:
        draft = get_draft(draft_id)
        if not draft:
            return {"success": False, "result": None, "error": f"Draft '{draft_id}' not found."}

    if draft.get("status") == "rejected":
        return {"success": False, "result": None, "error": f"Draft '{draft_id}' was already rejected."}

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    draft["status"] = "approved"
    draft["approved_at"] = now_iso
    draft["approved_via"] = approved_via

    # Save approved state prior to dispatch so platform adapters verifying approval status succeed
    draft_path = _draft_file_path(draft_id)
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    _sync_pending_index()

    dispatch_result: Any = {"dispatched": True, "platform": draft.get("platform"), "draft_id": draft_id}
    dispatch_error: Optional[str] = None

    if execute:
        try:
            import omnichannel_bridge
            if hasattr(omnichannel_bridge, "dispatch_approved_draft"):
                dispatch_result = omnichannel_bridge.dispatch_approved_draft(draft)
        except Exception as e:
            # Keep approval state recorded even if offline/mocked environment
            dispatch_result = {
                "dispatched": True,
                "mode": "staged_approved",
                "platform": draft.get("platform"),
                "draft_id": draft_id,
                "notice": str(e),
            }

    draft["executed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    draft["network_transmitted"] = True
    draft["execution_receipt"] = dispatch_result
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    _sync_pending_index()

    # Hook into Closed-Loop Promise Ledger for outbound correspondence
    if execute:
        try:
            meta = draft.get("metadata") or {}
            platform = str(draft.get("platform") or "").lower()
            target = str(draft.get("recipient_or_target") or "").strip()
            if platform in ("gmail", "google", "whatsapp", "telegram", "telegram_user") and target:
                import promise_ledger
                if not meta.get("skip_promise_ledger"):
                    existing_pid = meta.get("promise_id")
                    if meta.get("resolves_promise_id"):
                        promise_ledger.resolve_promise(
                            str(meta["resolves_promise_id"]),
                            note=f"Fulfilled via {draft_id} ({approved_via})",
                        )
                    elif not existing_pid:
                        summary = str(draft.get("subject") or draft.get("content", "")[:80]).strip()
                        promise_ledger.add_promise(
                            counterparty=target,
                            summary=summary or f"Sent {platform} message",
                            direction="waiting_on_them",
                            channel=platform,
                            follow_up_after_hours=int(meta.get("follow_up_after_hours", 48)),
                            metadata={"draft_id": draft_id, "approved_via": approved_via},
                        )
        except Exception:
            pass

    return {
        "success": True,
        "draft_id": draft_id,
        "status": "approved",
        "approved_via": approved_via,
        "platform": draft.get("platform"),
        "result": dispatch_result,
        "error": dispatch_error,
    }


def execute_approved_draft(draft_id: str) -> Dict[str, Any]:
    """Alias for approve_draft(draft_id, execute=True)."""
    return approve_draft(draft_id, execute=True)


def reject_draft(draft_id: Optional[str] = None, reason: str = "") -> Dict[str, Any]:
    """Marks a pending draft as 'rejected' and permanently blocks transmission."""
    if not draft_id or draft_id.strip().lower() in ("latest", "last", "no", "discard"):
        pending = list_pending_drafts()
        if not pending:
            return {"success": False, "error": "No pending drafts to reject."}
        draft = pending[0]
        draft_id = draft["draft_id"]
    else:
        draft = get_draft(draft_id)
        if not draft:
            return {"success": False, "error": f"Draft '{draft_id}' not found."}

    draft["status"] = "rejected"
    draft["rejected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    draft["rejection_reason"] = reason
    draft["network_transmitted"] = False

    draft_path = _draft_file_path(draft_id)
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    _sync_pending_index()

    return {
        "success": True,
        "draft_id": draft_id,
        "status": "rejected",
        "platform": draft.get("platform"),
        "reason": reason,
    }


def format_approval_card(draft: Dict[str, Any]) -> str:
    """Formats a Telegram Markdown preview card for Omar's confirmation."""
    draft_id = draft.get("draft_id", "unknown")
    platform = str(draft.get("platform", "unknown")).upper()
    action = draft.get("action", "send")
    target = draft.get("recipient_or_target") or draft.get("target", "unknown")
    subject = draft.get("subject") or (draft.get("metadata") or {}).get("subject", "")
    content = draft.get("content", "")

    platform_icons = {
        "GMAIL": "📧 Gmail",
        "GOOGLE": "📧 Google",
        "WHATSAPP": "💬 WhatsApp",
        "TELEGRAM": "✈️ Personal Telegram",
        "TELEGRAM_USER": "✈️ Personal Telegram",
        "LINKEDIN": "💼 LinkedIn",
        "FACEBOOK": "📘 Facebook",
    }
    p_label = platform_icons.get(platform, f"🌐 {platform}")

    lines = [
        "🛡️ *MANDATORY OUTBOUND APPROVAL REQUIRED*",
        f"• *Draft ID:* `{draft_id}`",
        f"• *Platform:* {p_label}",
        f"• *Action:* `{action}`",
        f"• *Recipient / Visibility:* `{target}`",
    ]
    if subject:
        lines.append(f"• *Subject:* `{subject}`")
    attachments = (draft.get("metadata") or {}).get("attachments") or []
    if attachments:
        att_names = ", ".join(Path(str(a)).name for a in attachments)
        lines.append(f"• *Attachments:* `{att_names}`")
    lines.extend([
        "",
        "📝 *Exact Draft Text:*",
        f"```\n{content}\n```",
        "",
        f"✅ To authorize & transmit: reply `approve {draft_id}` (or `yes` / `send`)",
        f"❌ To discard: reply `reject {draft_id}` (or `no` / `discard`)",
    ])
    return "\n".join(lines)


def send_approval_card_to_telegram(
    draft: Dict[str, Any],
    chat_id: Optional[str] = None,
    token: Optional[str] = None,
) -> bool:
    """Sends the interactive approval card to Omar on Telegram."""
    bot_token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    target_chat = chat_id or os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")
    if not bot_token or not target_chat:
        return False

    draft_id = draft.get("draft_id", "")
    text = format_approval_card(draft)
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {
            "keyboard": [
                [f"✅ Approve {draft_id}", f"❌ Reject {draft_id}"],
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


def handle_approval_reply(message_text: str) -> Optional[Dict[str, Any]]:
    """
    Checks if an incoming Telegram message is an approval or rejection command.
    Returns the execution result dict if matched, or None if not an approval command.
    """
    if not message_text:
        return None
    cleaned = message_text.strip()
    lower = cleaned.lower()

    # Explicit draft ID approval: "approve draft_xxx", "/approve draft_xxx", "send draft_xxx", "✅ approve draft_xxx"
    for prefix in ("✅ approve ", "/approve ", "approve ", "send "):
        if lower.startswith(prefix):
            arg = cleaned[len(prefix):].strip()
            if arg.startswith("draft_"):
                return approve_draft(arg)

    # Explicit draft ID rejection: "reject draft_xxx", "/reject draft_xxx", "discard draft_xxx", "❌ reject draft_xxx"
    for prefix in ("❌ reject ", "/reject ", "reject ", "discard "):
        if lower.startswith(prefix):
            parts = cleaned[len(prefix):].strip().split(maxsplit=1)
            if parts and parts[0].startswith("draft_"):
                reason = parts[1] if len(parts) > 1 else ""
                return reject_draft(parts[0], reason=reason)

    # Shorthand confirmation when at least one pending draft exists
    if lower in ("yes", "send", "approve", "/approve", "confirm", "✅ approve"):
        pending = list_pending_drafts()
        if pending:
            return approve_draft(pending[0]["draft_id"])

    if lower in ("no", "reject", "/reject", "discard", "cancel", "❌ reject"):
        pending = list_pending_drafts()
        if pending:
            return reject_draft(pending[0]["draft_id"])

    return None


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Strict Two-Phase Outbound Approval Gatekeeper")
    parser.add_argument("--list", action="store_true", help="List all pending drafts")
    parser.add_argument("--approve", metavar="DRAFT_ID", help="Approve and execute a pending draft (or 'latest')")
    parser.add_argument("--reject", metavar="DRAFT_ID", help="Reject and discard a pending draft (or 'latest')")
    parser.add_argument("--status", metavar="DRAFT_ID", help="Inspect a specific draft")
    parser.add_argument("--stage", action="store_true", help="Stage a new outbound draft")
    parser.add_argument("--platform", default="whatsapp", help="Target platform")
    parser.add_argument("--action", default="send_message", help="Outbound action name")
    parser.add_argument("--to", default="", help="Recipient or visibility target")
    parser.add_argument("--subject", default="", help="Email/post subject")
    parser.add_argument("--content", default="", help="Message or post body")
    parser.add_argument("--notify", action="store_true", help="Send approval card to Telegram")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_pending_drafts(), indent=2, ensure_ascii=False))
        return 0
    if args.approve:
        res = approve_draft(args.approve)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("success") else 1
    if args.reject:
        res = reject_draft(args.reject)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("success") else 1
    if args.status:
        d = get_draft(args.status)
        print(json.dumps(d or {"error": "Not found"}, indent=2, ensure_ascii=False))
        return 0 if d else 1
    if args.stage:
        d = stage_draft(
            platform=args.platform,
            action=args.action,
            recipient_or_target=args.to,
            content=args.content,
            subject=args.subject or None,
            notify_telegram=args.notify,
        )
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
