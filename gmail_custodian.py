#!/usr/bin/env python3
"""
🚨 Aethelgard Gmail Custodian Alias Module (R3)
Re-exports all VIP Inbox Triage & Vault Auto-Ingest functions from vip_inbox_triage.py.
"""

from vip_inbox_triage import (
    VIP_RULES,
    get_default_state_file,
    classify_vip_sender,
    is_vip_sender,
    format_vip_alert,
    send_vip_telegram_alert,
    ingest_vip_email_to_vault,
    process_incoming_email,
    run_vip_triage_cycle,
    main,
)

__all__ = [
    "VIP_RULES",
    "get_default_state_file",
    "classify_vip_sender",
    "is_vip_sender",
    "format_vip_alert",
    "send_vip_telegram_alert",
    "ingest_vip_email_to_vault",
    "process_incoming_email",
    "run_vip_triage_cycle",
    "main",
]

if __name__ == "__main__":
    import sys
    sys.exit(main())
