#!/usr/bin/env python3
"""
Aethelgard Calendar & Operational Agenda Bridge
===============================================
Zero-OAuth calendar fusion combining:
1. Google Calendar Private Secret `.ics` URL (stored outside git in ~/.aethelgard/credentials/calendar_config.json)
2. `.ics` invite attachments from recent emails (via GoogleAdapter)
3. Shift schedules from shift_tracker.py
Also generates valid RFC 5545 `.ics` invitations for email staging.
"""

import datetime
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DAMASCUS_TZ = datetime.timezone(datetime.timedelta(hours=3))


def _get_credentials_dir() -> Path:
    override = os.environ.get("AETHELGARD_CREDENTIALS_DIR")
    if override:
        p = Path(os.path.expanduser(override))
    else:
        home = os.environ.get("AETHELGARD_HOME")
        base = Path(os.path.expanduser(home)) if home else (Path.home() / ".aethelgard")
        p = base / "credentials"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_calendar_config_path() -> Path:
    return _get_credentials_dir() / "calendar_config.json"


def load_calendar_config() -> Dict[str, Any]:
    path = get_calendar_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_calendar_ics_url(ics_url: str, label: str = "Primary Google Calendar") -> Dict[str, Any]:
    """Saves private `.ics` feed URL safely outside the git workspace."""
    cfg = load_calendar_config()
    feeds = cfg.get("ics_feeds", [])
    if not isinstance(feeds, list):
        feeds = []
    # Deduplicate by URL
    feeds = [f for f in feeds if isinstance(f, dict) and f.get("url") != ics_url]
    feeds.insert(0, {
        "label": label,
        "url": ics_url.strip(),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    cfg["ics_feeds"] = feeds
    path = get_calendar_config_path()
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"saved": True, "path": str(path), "feeds": len(feeds)}


def _unfold_ics_lines(raw_text: str) -> List[str]:
    """Unfolds RFC 5545 folded lines (CRLF/LF followed by space or tab)."""
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    unfolded = re.sub(r"\n[ \t]", "", normalized)
    return unfolded.split("\n")


def _parse_ics_datetime(value: str, params: str = "") -> Optional[datetime.datetime]:
    """Parses RFC 5545 DATE / DATE-TIME strings into timezone-aware Damascus datetimes."""
    val = (value or "").strip()
    if not val:
        return None
    # Format: 20261116T140000Z
    if re.fullmatch(r"\d{8}T\d{6}Z", val):
        dt = datetime.datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(DAMASCUS_TZ)
    # Format: 20261116T140000
    if re.fullmatch(r"\d{8}T\d{6}", val):
        dt = datetime.datetime.strptime(val, "%Y%m%dT%H%M%S")
        if "UTC" in params.upper():
            return dt.replace(tzinfo=datetime.timezone.utc).astimezone(DAMASCUS_TZ)
        return dt.replace(tzinfo=DAMASCUS_TZ)
    # All-day DATE: 20261116
    if re.fullmatch(r"\d{8}", val):
        d = datetime.datetime.strptime(val, "%Y%m%d")
        return d.replace(tzinfo=DAMASCUS_TZ)
    return None


def _unescape_ics_text(text: str) -> str:
    return (
        text.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def parse_ics_events(raw_ics: str, source: str = "ics") -> List[Dict[str, Any]]:
    """Lightweight RFC 5545 VEVENT parser requiring zero external dependencies."""
    if not raw_ics or "BEGIN:VEVENT" not in raw_ics:
        return []
    events: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for line in _unfold_ics_lines(raw_ics):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "BEGIN:VEVENT":
            current = {"source": source}
            continue
        if stripped == "END:VEVENT":
            if current and current.get("start"):
                events.append(current)
            current = None
            continue
        if current is None or ":" not in stripped:
            continue

        key_part, val_part = stripped.split(":", 1)
        if ";" in key_part:
            prop_name, prop_params = key_part.split(";", 1)
        else:
            prop_name, prop_params = key_part, ""
        prop = prop_name.upper().strip()

        if prop == "SUMMARY":
            current["summary"] = _unescape_ics_text(val_part)
        elif prop == "DESCRIPTION":
            current["description"] = _unescape_ics_text(val_part)[:400]
        elif prop == "LOCATION":
            current["location"] = _unescape_ics_text(val_part)
        elif prop == "UID":
            current["uid"] = val_part.strip()
        elif prop == "STATUS":
            current["status"] = val_part.strip()
        elif prop == "DTSTART":
            dt = _parse_ics_datetime(val_part, prop_params)
            if dt:
                current["start"] = dt.isoformat()
                current["_start_ts"] = dt.timestamp()
                current["all_day"] = len(val_part.strip()) == 8
        elif prop == "DTEND":
            dt = _parse_ics_datetime(val_part, prop_params)
            if dt:
                current["end"] = dt.isoformat()

    events.sort(key=lambda e: e.get("_start_ts", 0.0))
    return events


def fetch_ics_url(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Aethelgard-CalendarBridge/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _load_shift_events(now: datetime.datetime) -> List[Dict[str, Any]]:
    """Extracts current / upcoming shift blocks from shift_tracker.py."""
    try:
        import shift_tracker
        state = shift_tracker.load_tracker_state()
    except Exception:
        return []

    events: List[Dict[str, Any]] = []
    shift_str = state.get("today_shift_raw") or ""
    date_str = state.get("date") or now.strftime("%Y-%m-%d")
    if not shift_str:
        return []

    m = re.search(r"(\d{1,2}:\d{2})\s*[-–→to]+\s*(\d{1,2}:\d{2})", str(shift_str))
    if not m:
        return []
    try:
        base_day = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=DAMASCUS_TZ)
        sh, sm = map(int, m.group(1).split(":"))
        eh, em = map(int, m.group(2).split(":"))
        start_dt = base_day.replace(hour=sh, minute=sm, second=0)
        end_dt = base_day.replace(hour=eh, minute=em, second=0)
        if end_dt <= start_dt:
            end_dt += datetime.timedelta(days=1)
        events.append({
            "source": "shift_tracker",
            "summary": f"Work Shift ({shift_str})",
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "_start_ts": start_dt.timestamp(),
            "all_day": False,
        })
    except Exception:
        pass
    return events


def get_unified_agenda(
    hours_ahead: int = 24,
    now: Optional[datetime.datetime] = None,
    include_email_ics: bool = True,
) -> Dict[str, Any]:
    """
    Merges Google Calendar `.ics` feeds, shift blocks, and optional Gmail `.ics` invites
    within `[now - 2h, now + hours_ahead]`.
    """
    ref_now = now or datetime.datetime.now(DAMASCUS_TZ)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=DAMASCUS_TZ)

    window_start = (ref_now - datetime.timedelta(hours=2)).timestamp()
    window_end = (ref_now + datetime.timedelta(hours=hours_ahead)).timestamp()

    all_events: List[Dict[str, Any]] = []
    feed_errors: List[str] = []

    cfg = load_calendar_config()
    feeds = cfg.get("ics_feeds", []) if isinstance(cfg, dict) else []
    configured_feeds = 0

    for feed in feeds:
        if not isinstance(feed, dict) or not feed.get("url"):
            continue
        configured_feeds += 1
        try:
            raw = fetch_ics_url(feed["url"])
            parsed = parse_ics_events(raw, source=feed.get("label", "google_calendar_ics"))
            all_events.extend(parsed)
        except Exception as exc:
            feed_errors.append(f"{feed.get('label', 'feed')}: {exc}")

    all_events.extend(_load_shift_events(ref_now))

    if include_email_ics and configured_feeds == 0:
        # Opportunistically check Gmail for calendar invite subjects cleanly without failing
        try:
            from omnichannel_bridge import GoogleAdapter
            ga = GoogleAdapter()
            if ga.get_status().get("status") == "connected":
                stubs = ga.search_emails(query="Invitation OR calendar OR interview", max_results=3)
                for s in stubs:
                    if s.get("subject"):
                        all_events.append({
                            "source": "gmail_hint",
                            "summary": f"Email Invite/Schedule: {s.get('subject')}",
                            "start": ref_now.isoformat(),
                            "_start_ts": ref_now.timestamp(),
                            "all_day": True,
                        })
        except Exception:
            pass

    filtered: List[Dict[str, Any]] = []
    seen_keys = set()
    for ev in all_events:
        ts = float(ev.get("_start_ts", 0.0))
        if window_start <= ts <= window_end:
            dedup_key = (ev.get("uid") or ev.get("summary", ""), ev.get("start", ""))
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            clean_ev = {k: v for k, v in ev.items() if not k.startswith("_")}
            filtered.append(clean_ev)

    filtered.sort(key=lambda e: e.get("start", ""))
    return {
        "now": ref_now.isoformat(),
        "hours_ahead": hours_ahead,
        "ics_feeds_configured": configured_feeds,
        "events": filtered,
        "errors": feed_errors,
    }


def format_agenda_summary(hours_ahead: int = 24, now: Optional[datetime.datetime] = None) -> str:
    agenda = get_unified_agenda(hours_ahead=hours_ahead, now=now, include_email_ics=False)
    events = agenda.get("events", [])
    if not events:
        if agenda.get("ics_feeds_configured", 0) == 0:
            return "No upcoming shifts or events in the next window (Google Calendar .ics feed not configured yet)."
        return f"Clear schedule for the next {hours_ahead} hours."
    lines = [f"Agenda (next {hours_ahead}h):"]
    for ev in events:
        start_short = str(ev.get("start", ""))[:16].replace("T", " ")
        loc = f" @ {ev['location']}" if ev.get("location") else ""
        lines.append(f"- {start_short} — {ev.get('summary', 'Event')}{loc} [{ev.get('source', 'cal')}]")
    return "\n".join(lines)


def generate_ics_invite(
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    location: str = "",
    uid: Optional[str] = None,
) -> str:
    """Generates a standard RFC 5545 `.ics` calendar invite string."""
    start_dt = datetime.datetime.fromisoformat(start_iso)
    end_dt = datetime.datetime.fromisoformat(end_iso)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=DAMASCUS_TZ)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=DAMASCUS_TZ)

    s_utc = start_dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    e_utc = end_dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ev_uid = uid or f"aethelgard-{stamp}@custodian"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Aethelgard Custodian//Jarvis v2//EN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{ev_uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{s_utc}",
        f"DTEND:{e_utc}",
        f"SUMMARY:{summary}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        escaped_desc = description.replace("\n", "\\n")
        lines.append(f"DESCRIPTION:{escaped_desc}")
    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    return "\r\n".join(lines)


def dispatch_calendar_invite(
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    location: str = "",
    method: str = "REQUEST",
) -> Dict[str, Any]:
    """Generates an RFC 5545 .ics payload and dispatches it via email to Omar's Gmail so Google Calendar ingests it."""
    from omnichannel_bridge import GoogleAdapter
    import smtplib
    from email.message import EmailMessage

    ga = GoogleAdapter()
    _, sender_email, app_pw = ga._has_oauth_and_app_pw()
    if not app_pw:
        return {"error": "Google credentials unavailable for calendar dispatch."}

    ics_content = generate_ics_invite(
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        description=description,
        location=location,
    )
    if method == "CANCEL":
        ics_content = ics_content.replace("METHOD:REQUEST", "METHOD:CANCEL")

    msg = EmailMessage()
    msg["From"] = sender_email
    msg["To"] = sender_email
    action_label = "Confirmed" if method == "REQUEST" else "Cancelled"
    msg["Subject"] = f"Calendar Event {action_label}: {summary}"
    msg.set_content(
        f"Aethelgard Calendar Sync:\n\n"
        f"Event: {summary}\n"
        f"Start: {start_iso}\n"
        f"End: {end_iso}\n"
        f"{f'Location: {location}\n' if location else ''}"
        f"{f'Description: {description}\n' if description else ''}"
    )

    msg.add_attachment(
        ics_content.encode("utf-8"),
        maintype="text",
        subtype="calendar",
        filename="invite.ics",
        params={"method": method, "charset": "UTF-8"},
    )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(sender_email, app_pw)
            server.send_message(msg)
        return {"success": True, "ics_sent": True, "method": method, "summary": summary}
    except Exception as exc:
        return {"error": f"Failed to send calendar invite: {exc}"}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Calendar & Agenda Bridge")
    parser.add_argument("--agenda", action="store_true", help="Show unified upcoming agenda")
    parser.add_argument("--hours", type=int, default=24, help="Lookahead hours (default: 24)")
    parser.add_argument("--set-ics", metavar="URL", help="Save private Google Calendar .ics URL")
    parser.add_argument("--label", default="Primary Google Calendar", help="Label for the .ics feed")
    args = parser.parse_args()

    if args.set_ics:
        print(json.dumps(save_calendar_ics_url(args.set_ics, label=args.label), indent=2))
        return 0
    if args.agenda:
        print(json.dumps(get_unified_agenda(hours_ahead=args.hours), indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
