#!/usr/bin/env python3
"""
🛡️ Aethelgard Outbound Gatekeeper Alias Module (R2)
Re-exports all two-phase approval primitives from approval_gatekeeper.py.
"""

from approval_gatekeeper import (
    SUPPORTED_PLATFORMS,
    get_drafts_dir,
    stage_draft,
    get_draft,
    list_pending_drafts,
    verify_draft_authorized,
    approve_draft,
    execute_approved_draft,
    reject_draft,
    format_approval_card,
    send_approval_card_to_telegram,
    handle_approval_reply,
    main,
)

__all__ = [
    "SUPPORTED_PLATFORMS",
    "get_drafts_dir",
    "stage_draft",
    "get_draft",
    "list_pending_drafts",
    "verify_draft_authorized",
    "approve_draft",
    "execute_approved_draft",
    "reject_draft",
    "format_approval_card",
    "send_approval_card_to_telegram",
    "handle_approval_reply",
    "main",
]

if __name__ == "__main__":
    import sys
    sys.exit(main())
