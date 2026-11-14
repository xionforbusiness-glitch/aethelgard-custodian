---
name: approval-gate
description: Mandatory two-phase human-in-the-loop Approval Gatekeeper that stages all outbound emails, WhatsApp messages, personal Telegram messages, LinkedIn posts, and Facebook posts with a unique draft ID and blocks transmission until Omar explicitly approves.
---

# Strict Outbound Approval Gatekeeper Skill

All external outbound actions across Gmail, WhatsApp, Personal Telegram, LinkedIn, and Facebook MUST pass through `approval_gatekeeper.py` (`outbound_gatekeeper.py`).

## Core Invariant
1. **Phase 1 — Stage Draft:** Every outbound request creates a pending draft (`draft_<id>`) in `~/.aethelgard/drafts/` with `status: "pending_approval"` and presents the preview card to Omar on Telegram. Zero network packets are sent to the external recipient during staging.
2. **Phase 2 — Explicit Authorization:** Only after Omar replies `"yes"`, `"send"`, `"approve <draft_id>"` (or taps the approval button) does `approve_draft(<draft_id>)` unlock and transmit the payload.

## Commands

```bash
# List all drafts awaiting Omar's confirmation
python approval_gatekeeper.py --list

# Stage a draft directly
python approval_gatekeeper.py --stage --platform whatsapp --action send_message --to "+963912345678" --content "Hello!" --notify

# Approve and dispatch a specific draft
python approval_gatekeeper.py --approve draft_xxxx

# Reject and discard a draft
python approval_gatekeeper.py --reject draft_xxxx
```
