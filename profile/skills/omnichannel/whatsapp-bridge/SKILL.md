---
name: whatsapp-bridge
description: Connect via lightweight multi-device companion bridge (8-digit phone pairing code or QR code) to read recent WhatsApp chats and stage outbound WhatsApp messages via the mandatory Approval Gatekeeper.
---

# WhatsApp Multi-Device Linked Bridge Skill

Use `omnichannel_bridge.py` (`WhatsAppAdapter`) to interact with Omar's WhatsApp account without headless Chromium (< 25MB RAM).

## Capabilities & Commands

### 1. Pair WhatsApp Companion Device (8-Digit Phone Code or QR)
```bash
python omnichannel_bridge.py whatsapp --pair "+963912345678"
```
Returns an 8-digit pairing code (`XXXX-XXXX`) that Omar enters in **WhatsApp → Linked Devices → Link with phone number instead**, plus a fallback QR payload.

### 2. List Recent Chats
```bash
python omnichannel_bridge.py whatsapp --chats
```

### 3. Stage an Outbound WhatsApp Message (Mandatory Approval Gate)
Never transmit a WhatsApp message without staging first:
```bash
python omnichannel_bridge.py whatsapp --stage-msg --to "+963912345678" --text "Message text"
```
Present the generated `draft_id`, recipient, and exact message to Omar and wait for his confirmation (`"yes"` / `"approve <draft_id>"`).
