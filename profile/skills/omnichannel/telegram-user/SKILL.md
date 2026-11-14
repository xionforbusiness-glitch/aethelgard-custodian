---
name: telegram-user
description: Connect via Telethon MTProto personal user session alongside @aethelgard_vault_bot to read dialogs and stage personal Telegram messages on Omar's behalf.
---

# Personal Telegram MTProto User Skill

Use `omnichannel_bridge.py` (`TelegramUserAdapter`) to operate Omar's personal Telegram account via MTProto alongside `@aethelgard_vault_bot` (< 20MB RAM).

## Capabilities & Commands

### 1. List Personal Dialogs & Chats
```bash
python omnichannel_bridge.py telegram --dialogs
```

### 2. Stage a Personal Telegram Message (Mandatory Approval Gate)
All outbound messages from Omar's personal Telegram account MUST be staged first:
```bash
python omnichannel_bridge.py telegram --stage-msg --to "@username_or_phone" --text "Exact message text"
```
Present the `draft_id`, target contact/channel, and exact text to Omar on `@aethelgard_vault_bot` and await his explicit approval before sending.
