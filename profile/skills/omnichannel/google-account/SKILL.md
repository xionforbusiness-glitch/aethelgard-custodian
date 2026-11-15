---
name: google-account
description: Read, search, summarize, and vault-ingest Gmail messages, inspect Google Calendar events and Google Drive files, and stage outbound emails via the mandatory two-phase Approval Gatekeeper.
---

# Google Account Skill (Gmail, Calendar, Drive)

Use `omnichannel_bridge.py` (`GoogleAdapter`) and `vip_inbox_triage.py` to interact with Omar's Google Account using zero headless browsers (< 12MB RAM).

## Capabilities & Commands

### 1. Check Latest, Search & Read Emails
```bash
python omnichannel_bridge.py google --latest 3
python omnichannel_bridge.py google --search "from:etihad.com OR is:unread"
python omnichannel_bridge.py google --read <MESSAGE_ID> [<MESSAGE_ID> ...]
```

### 2. Stage an Outbound Email (Two-Phase Approval Mandatory)
Never send an email directly without Omar's explicit confirmation. Always stage a draft first:
```bash
python omnichannel_bridge.py google --stage-email --to "recipient@example.com" --subject "Subject" --body "Exact email text"
```
Then present the `draft_id`, recipient, subject, and exact body to Omar on Telegram and wait for him to reply `"yes"`, `"send"`, or `"approve <draft_id>"`.

### 3. VIP Inbox Triage & Obsidian Vault Ingestion
```bash
python vip_inbox_triage.py --poll
```
Automatically detects VIP emails (Etihad Airways, University of the People, SVU), catalogs markdown notes into `raw/emails/` in the Obsidian Vault, and updates `index.md`.

### 4. Google Calendar & Google Drive
```bash
python omnichannel_bridge.py google --calendar
python omnichannel_bridge.py google --drive "training"
```
