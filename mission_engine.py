#!/usr/bin/env python3
"""
Aethelgard Persistent Parallel Mission Engine (V3)
==================================================
Manages long-horizon, multi-step autonomous missions across the day:
  - Persisted state in `~/.aethelgard/state/missions.json` + Obsidian Vault dossiers
  - Bounded `e2-micro` concurrency (`MAX_CONCURRENT_MISSIONS = 2`, global `PORTAL_BROWSER_LOCK = 1`)
  - Boot-time recovery of interrupted `queued`/`running` missions
  - Scoped zero-pause execution grants for active Omar-authorized missions
  - Consolidated Telegram milestone updates & completion phone callbacks (`SLEEP_SHIELD` aware)
"""

from __future__ import annotations

import contextlib
import datetime
import html as _html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional


MAX_CONCURRENT_MISSIONS = 2
DEFAULT_MAX_OUTBOUND_ACTIONS = 15
DEFAULT_MAX_STEPS = 16
PROXY_URL = os.environ.get("OMNIROUTE_URL", "http://127.0.0.1:20128/v1/chat/completions")
MISSION_MODELS = (
    "antigravity/gemini-3.8-flash-high",
    "antigravity/claude-sonnet-5-5-high",
    "antigravity/gemini-3.8-flash-low",
)

_STATE_LOCK = threading.RLock()
_MISSION_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_MISSIONS)
PORTAL_BROWSER_LOCK = threading.Semaphore(1)
_ACTIVE_WORKERS: Dict[str, threading.Thread] = {}


@contextlib.contextmanager
def portal_browser_lock(timeout: float = 180.0) -> Iterator[bool]:
    """Serializes headless Chromium execution globally across all missions (max 1 on e2-micro)."""
    acquired = PORTAL_BROWSER_LOCK.acquire(timeout=timeout)
    if not acquired:
        raise TimeoutError("Timed out waiting for exclusive headless browser lock.")
    try:
        yield True
    finally:
        PORTAL_BROWSER_LOCK.release()


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


def get_missions_path() -> Path:
    return _get_state_dir() / "missions.json"


def _resolve_vault_dir(vault_dir: Optional[str] = None) -> Optional[Path]:
    raw = vault_dir or os.environ.get("VAULT_DIR") or os.environ.get("OBSIDIAN_VAULT_PATH") or "~/vault"
    path = Path(os.path.expanduser(raw))
    return path if path.exists() else None


def _load_missions_unlocked() -> List[Dict[str, Any]]:
    path = get_missions_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("missions"), list):
            return data["missions"]
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_missions_unlocked(missions: List[Dict[str, Any]]) -> None:
    path = get_missions_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"missions": missions}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _write_mission_dossier(mission: Dict[str, Any], vault_dir: Optional[str] = None) -> Optional[str]:
    """Mirrors a clean Markdown mission dossier into `$VAULT_DIR/missions/<mission_id>.md`."""
    vault = _resolve_vault_dir(vault_dir)
    if not vault:
        return None
    missions_dir = vault / "missions"
    missions_dir.mkdir(parents=True, exist_ok=True)
    md_path = missions_dir / f"{mission['mission_id']}.md"

    scope = mission.get("autonomy_scope") or {}
    subtasks_lines = []
    for st in mission.get("subtasks") or []:
        if isinstance(st, dict):
            subtasks_lines.append(f"- **[{st.get('status', 'done')}]** {st.get('step', '')}: {st.get('detail', '')}")
        else:
            subtasks_lines.append(f"- {st}")

    artifact_lines = [f"- `{a}`" for a in (mission.get("artifacts") or [])]
    lines = [
        "---",
        f"mission_id: {mission['mission_id']}",
        f"title: \"{str(mission.get('title', '')).replace('\"', '')}\"",
        f"status: {mission.get('status')}",
        f"updated_at: {mission.get('updated_at')}",
        "---",
        f"# Mission: {mission.get('title')}",
        "",
        f"- **ID:** `{mission['mission_id']}`",
        f"- **Status:** `{mission.get('status')}`",
        f"- **Created:** `{mission.get('created_at')}`",
        f"- **Updated:** `{mission.get('updated_at')}`",
        f"- **Allowed Domains:** `{', '.join(scope.get('allowed_domains') or ['*'])}`",
        f"- **Allowed Channels:** `{', '.join(scope.get('allowed_channels') or ['gmail', 'portal'])}`",
        f"- **Auto Actions Executed:** `{scope.get('action_count', 0)} / {scope.get('max_outbound_actions', DEFAULT_MAX_OUTBOUND_ACTIONS)}`",
        "",
        "## Objective",
        mission.get("goal", ""),
        "",
        "## Execution Log",
        "\n".join(subtasks_lines) if subtasks_lines else "_No steps logged yet._",
        "",
    ]
    if artifact_lines:
        lines.extend(["## Artifacts & Evidence", "\n".join(artifact_lines), ""])
    if mission.get("result_summary"):
        lines.extend(["## Final Summary", str(mission["result_summary"]), ""])
    if mission.get("last_error"):
        lines.extend(["## Last Error / Blocker", str(mission["last_error"]), ""])

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return str(md_path)


def create_mission(
    title: str,
    goal: str,
    allowed_domains: Optional[List[str]] = None,
    allowed_channels: Optional[List[str]] = None,
    urgent: bool = False,
    max_outbound_actions: int = DEFAULT_MAX_OUTBOUND_ACTIONS,
    vault_dir: Optional[str] = None,
    allowed_targets: Optional[List[str]] = None,
    allowed_platforms: Optional[List[str]] = None,
    callback_when_done: bool = True,
    source: str = "",
) -> Dict[str, Any]:
    """Creates and persists a new MissionRecord in `queued` state."""
    eff_domains = allowed_domains if allowed_domains is not None else allowed_targets
    eff_channels = allowed_channels if allowed_channels is not None else allowed_platforms
    now_iso = _utc_now()
    mission: Dict[str, Any] = {
        "mission_id": f"msn_{uuid.uuid4().hex[:8]}",
        "title": (title or "Autonomous Mission").strip()[:120],
        "goal": (goal or title or "").strip(),
        "status": "queued",
        "created_at": now_iso,
        "updated_at": now_iso,
        "urgent": bool(urgent),
        "callback_when_done": bool(callback_when_done),
        "source": str(source or ""),
        "autonomy_scope": {
            "allowed_domains": [str(d).lower().strip() for d in (eff_domains or ["*"]) if str(d).strip()],
            "allowed_channels": [str(c).lower().strip() for c in (eff_channels or ["gmail", "portal", "linkedin", "whatsapp", "telegram"]) if str(c).strip()],
            "max_outbound_actions": max(1, int(max_outbound_actions)),
            "action_count": 0,
            "granted_at": now_iso,
        },
        "subtasks": [],
        "artifacts": [],
        "checkpoint_info": {},
        "result_summary": "",
        "last_error": "",
        "steps_completed": 0,
        "notified_completion": False,
    }
    with _STATE_LOCK:
        missions = _load_missions_unlocked()
        missions.append(mission)
        _save_missions_unlocked(missions)
    _write_mission_dossier(mission, vault_dir=vault_dir)
    return mission


def get_mission(mission_id: str) -> Optional[Dict[str, Any]]:
    with _STATE_LOCK:
        for m in _load_missions_unlocked():
            if m.get("mission_id") == mission_id:
                return dict(m)
    return None


def list_missions(status: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    with _STATE_LOCK:
        missions = _load_missions_unlocked()
    if status:
        want = status.lower().strip()
        missions = [m for m in missions if str(m.get("status", "")).lower() == want]
    missions.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return missions[:limit]


def update_mission(
    mission_id: str,
    vault_dir: Optional[str] = None,
    **updates: Any,
) -> Optional[Dict[str, Any]]:
    """Thread-safely updates fields on an existing MissionRecord and syncs Vault dossier."""
    updated: Optional[Dict[str, Any]] = None
    with _STATE_LOCK:
        missions = _load_missions_unlocked()
        for m in missions:
            if m.get("mission_id") == mission_id:
                for k, v in updates.items():
                    if k == "append_subtask" and isinstance(v, dict):
                        m.setdefault("subtasks", []).append(v)
                    elif k == "append_artifact" and v:
                        if v not in m.setdefault("artifacts", []):
                            m["artifacts"].append(str(v))
                    else:
                        m[k] = v
                m["updated_at"] = _utc_now()
                updated = dict(m)
                break
        if updated is not None:
            _save_missions_unlocked(missions)
    if updated is not None:
        _write_mission_dossier(updated, vault_dir=vault_dir)
    return updated


def _extract_domain(target: str) -> str:
    raw = (target or "").strip().lower()
    if "://" in raw:
        return (urllib.parse.urlparse(raw).hostname or "").lower()
    if "@" in raw:
        return raw.split("@", 1)[1].strip()
    return raw


def is_action_within_mission_scope(
    mission_id: Optional[str],
    platform: str,
    recipient_or_target: str,
    action_kind: str = "message",
) -> tuple[bool, str]:
    """
    Checks whether an autonomous outbound action or portal submission is covered
    by an active Omar-authorized Mission scope.
    """
    if not mission_id:
        return False, "No mission_id supplied"
    mission = get_mission(mission_id)
    if not mission:
        return False, f"Unknown mission_id: {mission_id}"
    if mission.get("status") not in ("running", "queued"):
        return False, f"Mission {mission_id} status is '{mission.get('status')}', not active"

    scope = mission.get("autonomy_scope") or {}
    count = int(scope.get("action_count") or 0)
    limit = int(scope.get("max_outbound_actions") or DEFAULT_MAX_OUTBOUND_ACTIONS)
    if count >= limit:
        return False, f"Mission {mission_id} reached max_outbound_actions ({limit})"

    plat = (platform or "").lower().strip()
    allowed_channels = [str(c).lower() for c in (scope.get("allowed_channels") or [])]
    aliases = {plat}
    if plat in ("google", "gmail"):
        aliases.update({"google", "gmail", "email"})
    elif plat in ("telegram", "telegram_user"):
        aliases.update({"telegram", "telegram_user"})
    if "*" not in allowed_channels and "all" not in allowed_channels and not (aliases & set(allowed_channels)):
        return False, f"Platform '{plat}' is outside mission {mission_id} allowed_channels={allowed_channels}"

    allowed_domains = [str(d).lower() for d in (scope.get("allowed_domains") or ["*"])]
    target_dom = _extract_domain(recipient_or_target)
    if "*" not in allowed_domains and "all" not in allowed_domains and target_dom:
        if not any(target_dom == dom or target_dom.endswith("." + dom) for dom in allowed_domains):
            return False, f"Target domain '{target_dom}' outside allowed_domains={allowed_domains}"

    return True, "authorized"


def record_mission_action(mission_id: str, summary: str, artifact: str = "", vault_dir: Optional[str] = None) -> None:
    """Increments action counter and logs an executed autonomous step on the mission."""
    with _STATE_LOCK:
        missions = _load_missions_unlocked()
        target_m = None
        for m in missions:
            if m.get("mission_id") == mission_id:
                scope = m.setdefault("autonomy_scope", {})
                scope["action_count"] = int(scope.get("action_count") or 0) + 1
                m.setdefault("subtasks", []).append({
                    "at": _utc_now(),
                    "step": "auto_action",
                    "status": "done",
                    "detail": summary[:240],
                })
                if artifact and artifact not in m.setdefault("artifacts", []):
                    m["artifacts"].append(artifact)
                m["updated_at"] = _utc_now()
                target_m = dict(m)
                break
        if target_m is not None:
            _save_missions_unlocked(missions)
    if target_m is not None:
        _write_mission_dossier(target_m, vault_dir=vault_dir)


def web_search_lightweight(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Low-RAM web search via DuckDuckGo HTML endpoint (fallback to Instant Answer JSON),
    avoiding Chromium overhead for simple information lookups.
    """
    q = (query or "").strip()
    if not q:
        return []
    encoded = urllib.parse.quote_plus(q)
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Safari/537.36"}
    results: List[Dict[str, str]] = []
    try:
        req = urllib.request.Request(f"https://html.duckduckgo.com/html/?q={encoded}", headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'(?:<a[^>]*class="result__snippet"[^>]*>(.*?)</a>|<div[^>]*class="result__snippet"[^>]*>(.*?)</div>)',
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )
        for href, title_html, snip1, snip2 in blocks[:max_results]:
            raw_url = _html.unescape(href)
            if "uddg=" in raw_url:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
                raw_url = qs.get("uddg", [raw_url])[0]
            title_clean = re.sub(r"<[^>]+>", "", _html.unescape(title_html)).strip()
            snippet_clean = re.sub(r"<[^>]+>", "", _html.unescape(snip1 or snip2 or "")).strip()
            results.append({"title": title_clean[:160], "url": raw_url[:400], "snippet": snippet_clean[:320]})
    except Exception:
        pass

    if not results:
        try:
            api_url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if data.get("AbstractText"):
                results.append({
                    "title": str(data.get("Heading") or q),
                    "url": str(data.get("AbstractURL") or ""),
                    "snippet": str(data.get("AbstractText"))[:320],
                })
            for rel in (data.get("RelatedTopics") or [])[:max_results]:
                if isinstance(rel, dict) and rel.get("Text"):
                    results.append({
                        "title": str(rel.get("Text", ""))[:90],
                        "url": str(rel.get("FirstURL", "")),
                        "snippet": str(rel.get("Text", ""))[:320],
                    })
        except Exception:
            pass
    return results[:max_results]


_MISSION_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for URLs, company career pages, contacts, or current facts without launching a browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords"},
                    "max_results": {"type": "integer", "description": "Up to 8 results"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "portal_apply_or_signup",
            "description": "Use Ephemeral Playwright to navigate a career/web portal, auto-create an account if needed, auto-verify Gmail OTP, upload Omar CV/Etihad certs, and submit the form. Automatically pauses cleanly on CAPTCHA/2FA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Target portal URL"},
                    "instructions": {"type": "string", "description": "What role to apply for or exact steps to take"},
                    "submit": {"type": "boolean", "description": "Whether to submit upon completion (default True inside mission)"},
                },
                "required": ["url", "instructions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_channel",
            "description": "Read recent messages across gmail, linkedin, whatsapp, or telegram.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "gmail, linkedin, whatsapp, or telegram"},
                    "query": {"type": "string", "description": "Optional search/filter string"},
                    "limit": {"type": "integer", "description": "Max items (default 5)"},
                },
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_scoped_message",
            "description": "Send an outbound email or message strictly within this authorized mission's scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "description": "gmail, whatsapp, telegram, or linkedin"},
                    "to": {"type": "string", "description": "Recipient email, phone, handle, or URN"},
                    "subject": {"type": "string", "description": "Email subject if gmail"},
                    "body": {"type": "string", "description": "Exact message body"},
                    "attach_career_pack": {"type": "boolean", "description": "Attach Omar CV + Etihad certificates"},
                },
                "required": ["platform", "to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_career_pack",
            "description": "Locate Omar's CVs/certificates and generate tailored cover letter / answers for a target role.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "role": {"type": "string"},
                    "job_description": {"type": "string"},
                },
                "required": ["company", "role"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_promise",
            "description": "Log a follow-up item or mark an existing commitment resolved in PromiseLedger.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "add or resolve"},
                    "counterparty": {"type": "string"},
                    "summary": {"type": "string"},
                    "direction": {"type": "string", "description": "waiting_on_them or waiting_on_me"},
                },
                "required": ["action", "counterparty", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_milestone",
            "description": "Send a concise progress ping to Omar on Telegram while continuing the mission.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_mission",
            "description": "Complete the mission with a clear executive summary for the phone callback and Vault dossier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Concise spoken & written summary of everything accomplished"},
                    "blocked_human": {"type": "boolean", "description": "True if waiting for Omar human CAPTCHA/2FA or external input"},
                },
                "required": ["summary"],
            },
        },
    },
]


def _post_proxy_completion(messages: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    api_key = os.environ.get("OMNIROUTE_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "sk-omni")
    payload = {
        "model": model,
        "messages": messages,
        "tools": _MISSION_TOOLS,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        PROXY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=75) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _dispatch_mission_tool(mission_id: str, name: str, args: Dict[str, Any], vault_dir: Optional[str]) -> Dict[str, Any]:
    if name == "web_search":
        hits = web_search_lightweight(str(args.get("query") or ""), max_results=int(args.get("max_results") or 5))
        return {"results": hits}

    if name == "prepare_career_pack":
        import job_application_engine
        assets = job_application_engine.locate_career_assets(vault_dir=vault_dir)
        app = job_application_engine.generate_tailored_application(
            company=str(args.get("company") or "Target Company"),
            role=str(args.get("role") or "Aviation Operations"),
            job_description=str(args.get("job_description") or ""),
            vault_dir=vault_dir,
        )
        return {"assets": assets, "application_pack": app}

    if name == "portal_apply_or_signup":
        import portal_automation
        url = str(args.get("url") or "").strip()
        instructions = str(args.get("instructions") or "").strip()
        submit = bool(args.get("submit", True))
        ok_scope, scope_reason = is_action_within_mission_scope(mission_id, "portal", url, action_kind="portal_apply")
        if not ok_scope:
            return {"success": False, "error": scope_reason}
        with portal_browser_lock():
            return portal_automation.run_portal_task(
                url=url,
                instructions=instructions,
                mission_id=mission_id,
                auto_submit=submit,
                vault_dir=vault_dir,
            )

    if name == "check_channel":
        import omnichannel_bridge
        ch = str(args.get("channel") or "gmail").lower().strip()
        q = str(args.get("query") or "").strip()
        lim = max(1, min(int(args.get("limit") or 5), 10))
        if ch in ("gmail", "google"):
            ga = omnichannel_bridge.GoogleAdapter()
            return {"emails": ga.search_emails(q, max_results=lim) if q else ga.get_latest_emails(count=lim)}
        if ch == "linkedin":
            la = omnichannel_bridge.LinkedInAdapter()
            return {"conversations": la.get_conversations(limit=lim)}
        if ch == "whatsapp":
            wa = omnichannel_bridge.WhatsAppAdapter()
            return {"chats": wa.list_recent_chats(limit=lim)}
        if ch == "telegram":
            ta = omnichannel_bridge.TelegramUserAdapter()
            return {"dialogs": ta.list_dialogs(limit=lim)}
        return {"error": f"Unsupported channel: {ch}"}

    if name == "send_scoped_message":
        import approval_gatekeeper
        import job_application_engine
        plat = str(args.get("platform") or "gmail").lower().strip()
        to = str(args.get("to") or "").strip()
        subj = str(args.get("subject") or "").strip() or None
        body = str(args.get("body") or "").strip()
        meta: Dict[str, Any] = {}
        if args.get("attach_career_pack"):
            assets = job_application_engine.locate_career_assets(vault_dir=vault_dir)
            att = []
            if assets.get("primary_cv"):
                att.append(assets["primary_cv"])
            att.extend((assets.get("certificates") or [])[:5])
            if att:
                meta["attachments"] = att
        return approval_gatekeeper.authorize_or_dispatch_mission_action(
            mission_id=mission_id,
            platform=plat,
            action="send_email" if plat in ("gmail", "google") else "send_message",
            recipient_or_target=to,
            content=body,
            subject=subj,
            metadata=meta or None,
            vault_dir=vault_dir,
        )

    if name == "manage_promise":
        import promise_ledger
        act = str(args.get("action") or "add").lower().strip()
        if act == "add":
            return promise_ledger.add_promise(
                counterparty=str(args.get("counterparty") or "FollowUp"),
                summary=str(args.get("summary") or ""),
                direction=str(args.get("direction") or "waiting_on_them"),
                channel="mission",
                vault_dir=vault_dir,
            )
        return {"info": "Specify promise_id to resolve or use promise_ledger CLI."}

    if name == "report_milestone":
        msg = str(args.get("message") or "").strip()
        if msg:
            update_mission(
                mission_id,
                vault_dir=vault_dir,
                append_subtask={"at": _utc_now(), "step": "milestone", "status": "done", "detail": msg},
            )
            try:
                from proactive_scheduler import send_telegram
                send_telegram(f"🧭 *Mission Update (`{mission_id}`):*\n{msg}")
            except Exception:
                pass
        return {"reported": True}

    if name == "finish_mission":
        return {"__finish__": True, "summary": str(args.get("summary") or "Completed."), "blocked_human": bool(args.get("blocked_human", False))}

    return {"error": f"Unknown mission tool: {name}"}


def _notify_mission_terminal(mission: Dict[str, Any], vault_dir: Optional[str] = None) -> None:
    """Delivers concise Telegram receipt + executive callback phone call respecting SLEEP_SHIELD."""
    mission_id = mission["mission_id"]
    status = mission.get("status", "completed")
    summary = str(mission.get("result_summary") or mission.get("last_error") or mission.get("title") or "")
    urgent = bool(mission.get("urgent"))

    allow_call = True
    try:
        import circadian_oracle
        op = circadian_oracle.get_operational_mode(vault_dir=vault_dir)
        allow_call = bool(op.get("allow_proactive_calls", True)) or urgent
    except Exception:
        pass

    icon = "✅" if status == "completed" else ("⏸️" if status == "waiting_human" else "⚠️")
    tg_text = (
        f"{icon} *Mission `{mission_id}` ({status.upper()})*\n"
        f"• *Title:* {mission.get('title')}\n"
        f"• *Outcome:* {summary[:1200]}"
    )
    try:
        from proactive_scheduler import send_telegram
        send_telegram(tg_text)
    except Exception:
        pass

    if allow_call and status in ("completed", "waiting_human", "failed"):
        try:
            import telegram_voice_caller
            spoken = f"Omar, mission '{mission.get('title')}' finished with status {status}. {summary[:260]}"
            telegram_voice_caller.request_call_via_rpc_or_direct(
                opening_prompt=spoken,
                urgent=urgent,
                vault_dir=vault_dir,
            )
        except Exception:
            pass
    update_mission(mission_id, vault_dir=vault_dir, notified_completion=True)


def execute_mission_worker(
    mission_id: str,
    vault_dir: Optional[str] = None,
    llm_caller: Optional[Callable[[List[Dict[str, Any]], str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Executes an autonomous multi-step Mission under the e2-micro semaphore."""
    acquired = _MISSION_SEMAPHORE.acquire(timeout=300)
    if not acquired:
        return update_mission(mission_id, vault_dir=vault_dir, status="failed", last_error="Timed out waiting for mission slot") or {}

    try:
        mission = get_mission(mission_id)
        if not mission:
            return {"error": f"Mission {mission_id} not found"}
        if mission.get("status") in ("cancelled", "completed"):
            return mission

        update_mission(mission_id, vault_dir=vault_dir, status="running", last_error="")
        caller = llm_caller or _post_proxy_completion

        sys_prompt = (
            "You are Aethelgard's autonomous Background Mission Agent working on Omar Elnemr's GCP server.\n"
            f"Mission ID: {mission_id}\n"
            f"Title: {mission.get('title')}\n"
            "Rules:\n"
            "1. Execute steps methodically using tools (`web_search`, `prepare_career_pack`, `portal_apply_or_signup`, `check_channel`, `send_scoped_message`, `manage_promise`, `report_milestone`).\n"
            "2. You ALREADY have Omar's authorization for scoped outreach & portal signup/submission within this mission.\n"
            "3. NEVER attempt to bypass CAPTCHAs/anti-bot checks. If `portal_apply_or_signup` returns `waiting_human`, call `finish_mission(blocked_human=True, summary=...)` immediately.\n"
            "4. When done, ALWAYS call `finish_mission(summary=...)` with clear factual results (never invent confirmations)."
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Objective:\n{mission.get('goal')}\n\nPrior progress: {json.dumps(mission.get('subtasks') or [])}"},
        ]

        steps = int(mission.get("steps_completed") or 0)
        final_summary = ""
        final_status = "completed"

        for _step_idx in range(DEFAULT_MAX_STEPS):
            cur = get_mission(mission_id)
            if not cur or cur.get("status") in ("paused", "cancelled"):
                return cur or {}

            resp_json = None
            last_exc = None
            for model in MISSION_MODELS:
                try:
                    resp_json = caller(messages, model)
                    if resp_json and resp_json.get("choices"):
                        break
                except Exception as exc:
                    last_exc = exc
            if not resp_json or not resp_json.get("choices"):
                raise RuntimeError(str(last_exc or "All Antigravity models failed during mission step"))

            msg = resp_json["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                final_summary = str(msg.get("content") or "").strip() or "Mission completed."
                break

            messages.append({
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": tool_calls,
            })

            done_now = False
            for tc in tool_calls:
                fn_name = str((tc.get("function") or {}).get("name") or "")
                raw_args = (tc.get("function") or {}).get("arguments") or "{}"
                try:
                    fn_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    fn_args = {}
                tool_res = _dispatch_mission_tool(mission_id, fn_name, fn_args, vault_dir)
                steps += 1

                if tool_res.get("status") == "waiting_human":
                    final_status = "waiting_human"
                    final_summary = str(tool_res.get("reason") or "Human CAPTCHA/verification checkpoint required.")
                    update_mission(
                        mission_id,
                        vault_dir=vault_dir,
                        status="waiting_human",
                        checkpoint_info=tool_res,
                        append_artifact=str(tool_res.get("screenshot_path") or ""),
                        append_subtask={"at": _utc_now(), "step": fn_name, "status": "waiting_human", "detail": final_summary},
                        steps_completed=steps,
                    )
                    done_now = True
                    break

                if tool_res.get("__finish__"):
                    final_status = "waiting_human" if tool_res.get("blocked_human") else "completed"
                    final_summary = str(tool_res.get("summary") or "Mission completed.")
                    done_now = True
                    break

                if tool_res.get("screenshot_path"):
                    update_mission(mission_id, vault_dir=vault_dir, append_artifact=str(tool_res["screenshot_path"]))
                update_mission(
                    mission_id,
                    vault_dir=vault_dir,
                    steps_completed=steps,
                    append_subtask={
                        "at": _utc_now(),
                        "step": fn_name,
                        "status": "done" if not tool_res.get("error") else "warn",
                        "detail": json.dumps(tool_res, ensure_ascii=False)[:220],
                    },
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
                    "name": fn_name,
                    "content": json.dumps(tool_res, ensure_ascii=False)[:4000],
                })

            if done_now:
                break

        if not final_summary:
            final_summary = f"Executed {steps} mission steps for '{mission.get('title')}'."
        done_mission = update_mission(
            mission_id,
            vault_dir=vault_dir,
            status=final_status,
            result_summary=final_summary,
            steps_completed=steps,
        ) or {}
        _notify_mission_terminal(done_mission, vault_dir=vault_dir)
        return done_mission
    except Exception as exc:
        err_mission = update_mission(
            mission_id,
            vault_dir=vault_dir,
            status="failed",
            last_error=str(exc),
        ) or {"mission_id": mission_id, "status": "failed", "last_error": str(exc)}
        _notify_mission_terminal(err_mission, vault_dir=vault_dir)
        return err_mission
    finally:
        _MISSION_SEMAPHORE.release()
        _ACTIVE_WORKERS.pop(mission_id, None)


def start_mission_background(mission_id: str, vault_dir: Optional[str] = None) -> bool:
    """Spawns a non-blocking daemon thread for an active/queued mission if not already running."""
    with _STATE_LOCK:
        existing = _ACTIVE_WORKERS.get(mission_id)
        if existing and existing.is_alive():
            return False
        t = threading.Thread(
            target=execute_mission_worker,
            args=(mission_id, vault_dir),
            daemon=True,
            name=f"MissionWorker-{mission_id}",
        )
        _ACTIVE_WORKERS[mission_id] = t
        t.start()
        return True


def create_and_start_mission(
    title: str,
    goal: str,
    allowed_domains: Optional[List[str]] = None,
    allowed_channels: Optional[List[str]] = None,
    urgent: bool = False,
    max_outbound_actions: int = DEFAULT_MAX_OUTBOUND_ACTIONS,
    vault_dir: Optional[str] = None,
    allowed_targets: Optional[List[str]] = None,
    allowed_platforms: Optional[List[str]] = None,
    callback_when_done: bool = True,
    source: str = "",
) -> Dict[str, Any]:
    """Creates a persisted MissionRecord and launches its background worker thread."""
    mission = create_mission(
        title=title,
        goal=goal,
        allowed_domains=allowed_domains,
        allowed_channels=allowed_channels,
        urgent=urgent,
        max_outbound_actions=max_outbound_actions,
        vault_dir=vault_dir,
        allowed_targets=allowed_targets,
        allowed_platforms=allowed_platforms,
        callback_when_done=callback_when_done,
        source=source,
    )
    start_mission_background(mission["mission_id"], vault_dir=vault_dir)
    return mission


def control_mission(mission_id: str, action: str, vault_dir: Optional[str] = None) -> Dict[str, Any]:
    """Controls an existing mission (`pause`, `resume`, `cancel`)."""
    act = (action or "").lower().strip()
    m = get_mission(mission_id)
    if not m:
        return {"success": False, "error": f"Mission {mission_id} not found"}
    if act == "pause":
        updated = update_mission(mission_id, vault_dir=vault_dir, status="paused")
        return {"success": True, "mission": updated}
    if act == "cancel":
        updated = update_mission(mission_id, vault_dir=vault_dir, status="cancelled")
        return {"success": True, "mission": updated}
    if act in ("resume", "restart"):
        extra = {"steps_completed": 0} if act == "restart" else {}
        updated = update_mission(mission_id, vault_dir=vault_dir, status="queued", last_error="", **extra)
        start_mission_background(mission_id, vault_dir=vault_dir)
        return {"success": True, "status": "queued", "mission": updated}
    return {"success": False, "error": f"Unsupported action '{action}'. Use pause, resume, restart, or cancel."}


def recover_interrupted_missions(vault_dir: Optional[str] = None) -> List[str]:
    """Called on daemon startup to resume missions interrupted by reboot/restart."""
    resumed: List[str] = []
    for m in list_missions(limit=50):
        if m.get("status") in ("running", "queued"):
            mid = m["mission_id"]
            if start_mission_background(mid, vault_dir=vault_dir):
                resumed.append(mid)
    return resumed


def tick_missions_maintenance(vault_dir: Optional[str] = None) -> Dict[str, Any]:
    """Ensures queued missions aren't stranded without an active worker thread."""
    launched = 0
    for m in list_missions(limit=30):
        if m.get("status") == "queued":
            if start_mission_background(m["mission_id"], vault_dir=vault_dir):
                launched += 1
    return {"checked": True, "restarted_queued": launched}


def handle_mission_command(command_text: str, vault_dir: Optional[str] = None) -> str:
    """Processes Telegram `/mission ...` or `/resume <id>` commands cleanly."""
    raw = (command_text or "").strip()
    lower = raw.lower()
    if lower.startswith("/resume"):
        parts = raw.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "Usage: `/resume <mission_id>`"
        res = control_mission(parts[1].strip(), "resume", vault_dir=vault_dir)
        if res.get("success"):
            return f"▶️ *Mission `{parts[1].strip()}` resumed* and queued in background workers."
        return f"⚠️ {res.get('error')}"

    parts = raw.split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "list"
    arg = parts[2].strip() if len(parts) > 2 else ""

    if sub in ("list", "status") and not arg:
        msns = list_missions(limit=8)
        if not msns:
            return "🧭 *Aethelgard Missions:* No missions recorded yet. Start one with `/mission run <goal>`."
        lines = ["🧭 *Aethelgard Active & Recent Missions:*"]
        for m in msns:
            lines.append(f"• `{m['mission_id']}` [{m.get('status')}] — *{m.get('title')}* ({m.get('steps_completed', 0)} steps)")
        return "\n".join(lines)

    if sub == "status" and arg:
        m = get_mission(arg)
        if not m:
            return f"⚠️ Mission `{arg}` not found."
        return (
            f"🧭 *Mission `{m['mission_id']}`* (`{m.get('status')}`)\n"
            f"• *Title:* {m.get('title')}\n"
            f"• *Steps:* {m.get('steps_completed', 0)}\n"
            f"• *Summary:* {m.get('result_summary') or m.get('last_error') or 'In progress...'}"
        )

    if sub in ("pause", "resume", "cancel") and arg:
        res = control_mission(arg, sub, vault_dir=vault_dir)
        return f"🧭 Mission `{arg}` -> *{sub}*: `{res.get('success')}` {res.get('error', '')}"

    if sub == "run" and arg:
        m = create_and_start_mission(title=arg[:70], goal=arg, vault_dir=vault_dir)
        return (
            f"🚀 *Autonomous Mission Launched (`{m['mission_id']}`)*\n"
            f"• *Objective:* {m['goal']}\n"
            "• Running in background workers (`max 2` concurrent). I'll ping updates and ring `+20` when finished."
        )

    return (
        "🧭 *Mission Commands:*\n"
        "• `/mission list` — List active & recent missions\n"
        "• `/mission status <id>` — Detailed mission state\n"
        "• `/mission run <goal>` — Start an autonomous mission\n"
        "• `/mission pause|resume|cancel <id>` — Control execution"
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Persistent Parallel Mission Engine")
    parser.add_argument("--list", action="store_true", help="List recent/active missions")
    parser.add_argument("--status", metavar="MISSION_ID", help="Show mission details")
    parser.add_argument("--create", metavar="TITLE", help="Create and start a mission")
    parser.add_argument("--goal", default="", help="Mission objective (with --create)")
    parser.add_argument("--pause", metavar="MISSION_ID", help="Pause a mission")
    parser.add_argument("--resume", metavar="MISSION_ID", help="Resume a mission")
    parser.add_argument("--cancel", metavar="MISSION_ID", help="Cancel a mission")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_missions(limit=20), indent=2, ensure_ascii=False))
        return 0
    if args.status:
        m = get_mission(args.status)
        print(json.dumps(m or {"error": "Not found"}, indent=2, ensure_ascii=False))
        return 0 if m else 1
    if args.create:
        m = create_and_start_mission(title=args.create, goal=args.goal or args.create)
        print(json.dumps(m, indent=2, ensure_ascii=False))
        return 0
    for act, val in (("pause", args.pause), ("resume", args.resume), ("cancel", args.cancel)):
        if val:
            res = control_mission(val, act)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res.get("success") else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
