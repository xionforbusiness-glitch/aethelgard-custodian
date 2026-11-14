---
name: linkedin-account
description: Read Omar's LinkedIn feed and notifications and stage LinkedIn posts for publication via the mandatory two-phase Approval Gatekeeper.
---

# LinkedIn Account Skill

Use `omnichannel_bridge.py` (`LinkedInAdapter`) to read LinkedIn updates and stage professional posts using lightweight Voyager REST requests (< 10MB RAM).

## Capabilities & Commands

### 1. Read Feed & Notifications
```bash
python omnichannel_bridge.py linkedin --feed
python omnichannel_bridge.py linkedin --notifications
```

### 2. Stage a LinkedIn Post (Mandatory Approval Gate)
Never publish a post directly without staging:
```bash
python omnichannel_bridge.py linkedin --stage-post "Post content here" --visibility PUBLIC
```
Show the staged `draft_id`, visibility (`PUBLIC` or `CONNECTIONS`), and exact post copy to Omar on Telegram and wait for confirmation (`"yes"` / `"approve <draft_id>"`).
