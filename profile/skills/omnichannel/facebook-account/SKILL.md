---
name: facebook-account
description: Read Omar's Facebook feed, notifications, and reels, and stage Facebook posts for publication via the mandatory two-phase Approval Gatekeeper.
---

# Facebook Account Skill

Use `omnichannel_bridge.py` (`FacebookAdapter`) to inspect Omar's Facebook feed/notifications/reels and stage posts via lightweight session REST requests (< 10MB RAM).

## Capabilities & Commands

### 1. Read Feed, Notifications & Reels
```bash
python omnichannel_bridge.py facebook --feed
python omnichannel_bridge.py facebook --notifications
python omnichannel_bridge.py facebook --reels
```
For deep extraction and transcription of any Facebook Reel URL (`facebook.com/share/r/...` or `/reel/...`), invoke `python reel_and_url_ingest.py "<URL>" --vault --json`.

### 2. Stage a Facebook Post (Mandatory Approval Gate)
Never publish a Facebook post without staging first:
```bash
python omnichannel_bridge.py facebook --stage-post "Post text here" --audience PUBLIC
```
Present the `draft_id`, audience (`PUBLIC` or `FRIENDS`), and exact post text to Omar and wait for explicit approval.
