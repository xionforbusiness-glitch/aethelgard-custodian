#!/usr/bin/env python3
"""
Aethelgard Closed-Loop Promise & Deal Ledger
============================================
Tracks real-world commitments across emails, job applications, messages, and calls:
- `waiting_on_them`: Sent application/proposal/inquiry awaiting counterparty reply
- `waiting_on_me`: Obligation or reply Omar owes someone else
- `resolved` / `cancelled`: Closed loops with audit timestamps

Automatically detects SLA breaches (`follow_up_after_hours`), resolves open loops
when an inbound email matches a tracked counterparty/subject, and stages polite
follow-up drafts via `approval_gatekeeper.py`.
"""

import datetime
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _get_state_dir() -> Path:
    override = os.environ.get("AETHELGARD_STATE_DIR")
    if override:
        p = Path(os.path.expanduser(override))
    else:
        home = os.environ.get("AETHELGARD_HOME")
        base = Path(os.path.expanduser(home)) if home else (Path.home() / ".aethelgard")
        p = base / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_ledger_path() -> Path:
    return _get_state_dir() / "promise_ledger.json"


def _load_entries() -> List[Dict[str, Any]]:
    path = get_ledger_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("promises"), list):
            return data["promises"]
    except Exception:
        pass
    return []


def _save_entries(entries: List[Dict[str, Any]], vault_dir: Optional[str] = None) -> None:
    path = get_ledger_path()
    path.write_text(json.dumps({"promises": entries}, indent=2, ensure_ascii=False), encoding="utf-8")
    sync_ledger_to_vault(entries, vault_dir=vault_dir)


def sync_ledger_to_vault(entries: Optional[List[Dict[str, Any]]] = None, vault_dir: Optional[str] = None) -> Optional[str]:
    """Mirrors active & recently resolved commitments to `$VAULT_DIR/tasks/promise_ledger.md`."""
    target_vault = vault_dir or os.environ.get("VAULT_DIR")
    if not target_vault:
        return None
    vault_path = Path(os.path.expanduser(target_vault))
    if not vault_path.exists():
        return None

    rows = entries if entries is not None else _load_entries()
    tasks_dir = vault_path / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    out_file = tasks_dir / "promise_ledger.md"

    lines = [
        "---",
        'title: "Aethelgard Promise & Deal Ledger"',
        f'updated_at: "{datetime.datetime.now(datetime.timezone.utc).isoformat()}"',
        "---",
        "",
        "# 🤝 Closed-Loop Promise & Deal Ledger",
        "",
        "| ID | Counterparty | Direction | Summary | Status | SLA (h) | Due / Updated |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for item in rows:
        lines.append(
            f"| `{item.get('id')}` | {item.get('counterparty')} | `{item.get('direction')}` | "
            f"{str(item.get('summary', '')).replace('|', '/')} | `{item.get('status')}` | "
            f"{item.get('follow_up_after_hours', 48)} | {item.get('due_at') or item.get('updated_at', '')[:16]} |"
        )
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out_file)


def add_promise(
    counterparty: str,
    summary: str,
    direction: str = "waiting_on_them",
    channel: str = "gmail",
    follow_up_after_hours: int = 48,
    due_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates and persists a new commitment entry in the Promise Ledger."""
    entries = _load_entries()
    now = datetime.datetime.now(datetime.timezone.utc)
    pid = f"prm_{uuid.uuid4().hex[:8]}"
    entry: Dict[str, Any] = {
        "id": pid,
        "counterparty": counterparty.strip(),
        "summary": summary.strip(),
        "direction": direction if direction in ("waiting_on_them", "waiting_on_me") else "waiting_on_them",
        "channel": channel,
        "status": "open",
        "follow_up_after_hours": int(follow_up_after_hours),
        "due_at": due_at or "",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "followup_draft_id": None,
        "resolution_note": "",
        "metadata": metadata or {},
    }
    entries.insert(0, entry)
    _save_entries(entries, vault_dir=vault_dir)
    return entry


def list_promises(status: Optional[str] = "open") -> List[Dict[str, Any]]:
    entries = _load_entries()
    if status is None or status == "all":
        return entries
    return [e for e in entries if e.get("status") == status]


def resolve_promise(promise_id: str, note: str = "", vault_dir: Optional[str] = None) -> Dict[str, Any]:
    entries = _load_entries()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for item in entries:
        if item.get("id") == promise_id:
            item["status"] = "resolved"
            item["resolved_at"] = now_iso
            item["updated_at"] = now_iso
            item["resolution_note"] = note
            _save_entries(entries, vault_dir=vault_dir)
            return {"success": True, "promise": item}
    return {"success": False, "error": f"Promise '{promise_id}' not found."}


def check_overdue_promises(now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Returns open commitments whose SLA (`follow_up_after_hours` or `due_at`) has elapsed."""
    ref_now = now or datetime.datetime.now(datetime.timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=datetime.timezone.utc)

    overdue: List[Dict[str, Any]] = []
    for item in list_promises(status="open"):
        due_str = item.get("due_at")
        if due_str:
            try:
                due_dt = datetime.datetime.fromisoformat(due_str)
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=datetime.timezone.utc)
                if ref_now >= due_dt:
                    overdue.append(item)
                    continue
            except Exception:
                pass
        created_str = item.get("created_at") or item.get("updated_at")
        sla_hours = float(item.get("follow_up_after_hours", 48) or 48)
        if created_str:
            try:
                created_dt = datetime.datetime.fromisoformat(created_str)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=datetime.timezone.utc)
                elapsed_hours = (ref_now - created_dt).total_seconds() / 3600.0
                if elapsed_hours >= sla_hours:
                    item_copy = dict(item)
                    item_copy["elapsed_hours"] = round(elapsed_hours, 1)
                    overdue.append(item_copy)
            except Exception:
                pass
    return overdue


def _extract_email_domain(sender: str) -> str:
    m = re.search(r"@([A-Za-z0-9.\-]+)", sender or "")
    return m.group(1).lower() if m else ""


def ingest_inbound_email(
    email_data: Dict[str, Any],
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Matches an incoming email against open `waiting_on_them` commitments by
    exact counterparty email/handle, domain, or strong subject overlap, and auto-resolves.
    """
    sender = str(email_data.get("sender") or "").lower()
    subject = str(email_data.get("subject") or "").lower()
    if not sender and not subject:
        return {"matched": 0, "resolved_ids": []}

    sender_domain = _extract_email_domain(sender)
    entries = _load_entries()
    resolved_ids: List[str] = []
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for item in entries:
        if item.get("status") != "open" or item.get("direction") != "waiting_on_them":
            continue
        cp = str(item.get("counterparty") or "").lower().strip()
        if not cp:
            continue
        cp_domain = _extract_email_domain(cp)
        direct_match = cp in sender or (cp_domain and cp_domain == sender_domain and cp_domain not in ("gmail.com", "outlook.com", "yahoo.com"))
        # For consumer webmail domains, require full email handle or company token match in sender/subject
        summary_tokens = [w for w in re.findall(r"[a-z0-9]{4,}", str(item.get("summary", "")).lower()) if w not in ("email", "reply", "follow", "draft", "application")]
        token_hit = bool(summary_tokens) and any(t in subject for t in summary_tokens[:3])
        if direct_match or (cp in subject) or (sender_domain in ("gmail.com", "outlook.com") and cp in sender and token_hit):
            item["status"] = "resolved"
            item["resolved_at"] = now_iso
            item["updated_at"] = now_iso
            item["resolution_note"] = f"Auto-resolved by inbound email from {sender}: {subject[:80]}"
            resolved_ids.append(item["id"])

    if resolved_ids:
        _save_entries(entries, vault_dir=vault_dir)

    return {"matched": len(resolved_ids), "resolved_ids": resolved_ids}


def stage_followup_for_overdue(
    promise_id: str,
    notify_telegram: bool = True,
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Stages a polite follow-up email/message draft via approval_gatekeeper without auto-sending."""
    import approval_gatekeeper

    entries = _load_entries()
    target_item = next((e for e in entries if e.get("id") == promise_id), None)
    if not target_item:
        return {"success": False, "error": f"Promise '{promise_id}' not found."}
    if target_item.get("followup_draft_id"):
        return {"success": True, "already_staged": True, "draft_id": target_item["followup_draft_id"]}

    channel = target_item.get("channel") or "gmail"
    platform = "gmail" if channel in ("google", "gmail", "email") else channel
    recipient = target_item.get("counterparty", "")
    summary = target_item.get("summary", "our previous correspondence")
    subject = f"Follow-up: {summary[:70]}"
    body = (
        f"Dear Team / {recipient},\n\n"
        f"I hope you are having a productive week.\n\n"
        f"I am writing to kindly follow up regarding {summary}. "
        f"Please let me know if any additional documents or details from my side would be helpful.\n\n"
        f"Best regards,\nOmar Elnemr"
    )
    draft = approval_gatekeeper.stage_draft(
        platform=platform,
        action="send_email" if platform == "gmail" else "send_message",
        recipient_or_target=recipient,
        content=body,
        subject=subject,
        metadata={"promise_id": promise_id, "auto_followup": True},
        notify_telegram=notify_telegram,
    )
    target_item["followup_draft_id"] = draft["draft_id"]
    target_item["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _save_entries(entries, vault_dir=vault_dir)
    return {"success": True, "draft": draft, "promise_id": promise_id}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Closed-Loop Promise & Deal Ledger")
    parser.add_argument("--list", action="store_true", help="List open commitments")
    parser.add_argument("--all", action="store_true", help="Include resolved commitments")
    parser.add_argument("--overdue", action="store_true", help="List overdue commitments")
    parser.add_argument("--add", action="store_true", help="Add a commitment")
    parser.add_argument("--counterparty", default="", help="Email, company, or person")
    parser.add_argument("--summary", default="", help="Summary of promise")
    parser.add_argument("--direction", default="waiting_on_them", choices=["waiting_on_them", "waiting_on_me"])
    parser.add_argument("--sla-hours", type=int, default=48)
    parser.add_argument("--resolve", metavar="PROMISE_ID", help="Mark a promise resolved")
    parser.add_argument("--note", default="", help="Resolution note")
    args = parser.parse_args()

    if args.add:
        print(json.dumps(add_promise(
            counterparty=args.counterparty,
            summary=args.summary,
            direction=args.direction,
            follow_up_after_hours=args.sla_hours,
        ), indent=2, ensure_ascii=False))
        return 0
    if args.resolve:
        print(json.dumps(resolve_promise(args.resolve, note=args.note), indent=2, ensure_ascii=False))
        return 0
    if args.overdue:
        print(json.dumps(check_overdue_promises(), indent=2, ensure_ascii=False))
        return 0
    if args.list or args.all:
        print(json.dumps(list_promises(status="all" if args.all else "open"), indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
