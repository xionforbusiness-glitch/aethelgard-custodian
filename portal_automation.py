#!/usr/bin/env python3
"""
Aethelgard Ephemeral Playwright Web Portal & Auto-Signup Engine (V3)
====================================================================
Drives JavaScript-rendered web & career portals (e.g., Etihad Careers / Workday / Taleo)
on a 1GB GCP e2-micro VM without leaking idle browser memory:
  - Ephemeral headless Chromium (`chromium-headless-shell` compatible, single-process flags),
    strictly closed in `finally:` after every run (`0 MB` idle RSS).
  - Encrypted portal account storage in `~/.aethelgard/credentials/portal_accounts.json`.
  - Automatic account signup + live Gmail verification link / OTP code resolution via `GoogleAdapter`.
  - Automatic CV PDF & Etihad certificate upload via `job_application_engine.locate_career_assets()`.
  - Strict Anti-CAPTCHA Human Handoff: NEVER solves or bypasses CAPTCHAs, Turnstile, or 2FA checks;
    pauses cleanly, screenshots to Telegram, and returns `status="waiting_human"`.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


PROXY_URL = os.environ.get("OMNIROUTE_URL", "http://127.0.0.1:20128/v1/chat/completions")
PORTAL_LLM_MODEL = os.environ.get("PORTAL_LLM_MODEL", "antigravity/gemini-3.8-flash-high")

LOW_RAM_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--single-process",
    "--renderer-process-limit=1",
    "--disable-gpu",
    "--disable-extensions",
    "--mute-audio",
    "--no-first-run",
    "--disable-background-networking",
]

_CAPTCHA_PATTERNS = [
    r"\brecaptcha\b",
    r"\bhcaptcha\b",
    r"\bcf-challenge\b",
    r"\bturnstile\b",
    r"challenges\.cloudflare\.com",
    r"verify\s+you\s+are\s+human",
    r"i\s+am\s+not\s+a\s+robot",
    r"complete\s+the\s+captcha",
    r"\barkose\b",
    r"\bfuncaptcha\b",
    r"security\s+check\s+to\s+proceed",
    r"enter\s+(?:the\s+)?(?:sms|phone)\s+verification\s+code",
    r"two-factor\s+authentication\s+code",
]
_CAPTCHA_REGEX = re.compile("|".join(_CAPTCHA_PATTERNS), re.IGNORECASE)


def _get_credentials_dir() -> Path:
    env_dir = os.environ.get("AETHELGARD_CREDENTIALS_DIR")
    if env_dir:
        cred_path = Path(os.path.expanduser(env_dir))
    else:
        home = os.environ.get("AETHELGARD_HOME")
        base = Path(os.path.expanduser(home)) if home else (Path.home() / ".aethelgard")
        cred_path = base / "credentials"
    cred_path.mkdir(parents=True, exist_ok=True)
    return cred_path


def get_portal_accounts_path() -> Path:
    return _get_credentials_dir() / "portal_accounts.json"


def _get_vault_key() -> bytes:
    env_key = os.environ.get("AETHELGARD_VAULT_KEY", "").strip()
    if env_key:
        return hashlib.sha256(env_key.encode("utf-8")).digest()
    key_path = _get_credentials_dir() / ".vault_key"
    if key_path.exists():
        raw = key_path.read_text(encoding="utf-8").strip()
        if raw:
            return hashlib.sha256(raw.encode("utf-8")).digest()
    secret = secrets.token_hex(32)
    key_path.write_text(secret, encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _encrypt_secret(plaintext: str) -> str:
    key = _get_vault_key()
    nonce = os.urandom(16)
    data = plaintext.encode("utf-8")
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        stream.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    cipher = bytes(b ^ stream[i] for i, b in enumerate(data))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + tag + cipher).decode("ascii")


def _decrypt_secret(token: str) -> str:
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    if len(raw) < 32:
        raise ValueError("Corrupted encrypted secret")
    nonce, tag, cipher = raw[:16], raw[16:32], raw[32:]
    key = _get_vault_key()
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Vault key mismatch or tampered credential")
    stream = bytearray()
    counter = 0
    while len(stream) < len(cipher):
        stream.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(b ^ stream[i] for i, b in enumerate(cipher)).decode("utf-8")


def _normalize_domain(url_or_domain: str) -> str:
    raw = (url_or_domain or "").strip().lower()
    if "://" in raw:
        raw = urllib.parse.urlparse(raw).hostname or raw
    raw = re.sub(r"^www\.", "", raw)
    return re.sub(r"[^a-z0-9.\-]", "", raw) or "unknown"


def generate_strong_password() -> str:
    """Generates a portal-compliant random password with upper, lower, digits, and symbol."""
    return f"Ag#{secrets.token_urlsafe(12)}9!"


def save_portal_credential(
    domain_or_url: str,
    email: str,
    password: str,
    username: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Saves portal login credentials with encrypted password in `~/.aethelgard/credentials/portal_accounts.json`."""
    domain = _normalize_domain(domain_or_url)
    path = get_portal_accounts_path()
    accounts: Dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                accounts = loaded
        except Exception:
            pass
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    accounts[domain] = {
        "domain": domain,
        "email": email,
        "username": username or email,
        "password_enc": _encrypt_secret(password),
        "updated_at": now_iso,
        "metadata": metadata or {},
    }
    path.write_text(json.dumps(accounts, indent=2, ensure_ascii=False), encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    return {"domain": domain, "email": email, "username": username or email, "saved": True}


def get_portal_credential(domain_or_url: str) -> Optional[Dict[str, Any]]:
    """Loads and decrypts stored portal credentials if present."""
    domain = _normalize_domain(domain_or_url)
    path = get_portal_accounts_path()
    if not path.exists():
        return None
    try:
        accounts = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    rec = accounts.get(domain)
    if not isinstance(rec, dict):
        for k, v in accounts.items():
            if (domain in k or k in domain) and isinstance(v, dict):
                rec = v
                break
    if not isinstance(rec, dict) or not rec.get("password_enc"):
        return None
    try:
        plain = _decrypt_secret(rec["password_enc"])
    except Exception:
        return None
    return {
        "domain": rec.get("domain", domain),
        "email": rec.get("email", ""),
        "username": rec.get("username", ""),
        "password": plain,
        "updated_at": rec.get("updated_at", ""),
        "metadata": rec.get("metadata") or {},
    }


def detect_human_verification_gate(
    title: str = "",
    html_or_text: str = "",
    url: str = "",
) -> Tuple[bool, str]:
    """
    Detects CAPTCHAs, Cloudflare Turnstile, or SMS/2FA human-verification gates.
    NEVER attempts to solve or bypass — caller must pause cleanly for Omar.
    """
    combined = f"{title}\n{url}\n{html_or_text[:15000]}"
    m = _CAPTCHA_REGEX.search(combined)
    if m:
        return True, f"Human verification / anti-bot gate detected ({m.group(0).strip()})"
    return False, ""


def _extract_verification_artifact(subject: str, body: str) -> Dict[str, str]:
    """Extracts either a verification HTTPS link or 4-8 digit OTP code from an email."""
    text = f"{subject}\n{body}"
    urls = re.findall(r"https?://[^\s<>\"')]+", text)
    verif_url = ""
    for u in urls:
        low = u.lower()
        if any(k in low for k in ("verify", "confirm", "activate", "token", "validate", "email-verification")):
            verif_url = u.rstrip(".,;")
            break

    otp = ""
    otp_match = re.search(
        r"(?:code|otp|pin|passcode|verification|رمز|تفعيل)\D{0,24}\b([0-9]{4,8})\b",
        text,
        flags=re.IGNORECASE,
    )
    if otp_match:
        otp = otp_match.group(1)
    elif not verif_url:
        fallback_digits = re.search(r"\b([0-9]{6})\b", text)
        if fallback_digits:
            otp = fallback_digits.group(1)

    return {"verification_url": verif_url, "otp_code": otp}


def poll_gmail_verification(
    domain_or_url: str,
    timeout_sec: float = 90.0,
    poll_interval: float = 5.0,
    google_adapter: Any = None,
    email_fetcher: Optional[Callable[[], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """
    Polls Omar's Gmail inbox for an account verification link or OTP code matching `domain_or_url`.
    """
    domain = _normalize_domain(domain_or_url)
    skip_tokens = {"www", "careers", "jobs", "apply", "login", "auth", "portal", "app", "mail", "com", "org", "net", "aero", "ae", "sy", "co", "uk"}
    domain_tokens = [p for p in domain.split(".") if p and p not in skip_tokens and len(p) >= 2]
    brand = domain_tokens[0] if domain_tokens else (domain.split(".")[0] if "." in domain else domain)
    deadline = time.monotonic() + max(1.0, float(timeout_sec))

    while True:
        candidates: List[Dict[str, Any]] = []
        try:
            if email_fetcher is not None:
                candidates = list(email_fetcher() or [])
            else:
                ga = google_adapter
                if ga is None:
                    import omnichannel_bridge
                    ga = omnichannel_bridge.GoogleAdapter()
                hits = ga.search_emails(brand, max_results=6) or []
                for item in hits:
                    if not item.get("body") and item.get("id"):
                        try:
                            item = ga.read_email(str(item["id"]), max_body_chars=6000)
                        except Exception:
                            pass
                    candidates.append(item)
                if not candidates:
                    candidates = ga.get_latest_emails(count=5, max_body_chars=6000) or []
        except Exception:
            candidates = []

        for mail in candidates:
            sender = str(mail.get("sender") or "").lower()
            subj = str(mail.get("subject") or "")
            body = str(mail.get("body") or mail.get("snippet") or "")
            hay = f"{sender}\n{subj}\n{body}".lower()
            brand_hit = any(tok in hay for tok in domain_tokens) if domain_tokens else (brand and brand in hay)
            if brand_hit or any(w in hay for w in ("verify", "verification", "confirm", "otp", "passcode", "activate", "code", "رمز", "تفعيل")):
                extracted = _extract_verification_artifact(subj, body)
                if extracted["verification_url"] or extracted["otp_code"]:
                    return {
                        "found": True,
                        "subject": subj,
                        "sender": sender,
                        "verification_url": extracted["verification_url"],
                        "otp_code": extracted["otp_code"],
                    }

        if time.monotonic() >= deadline:
            return {"found": False, "error": f"No verification link or OTP found for {domain} within {timeout_sec}s"}
        time.sleep(max(0.1, float(poll_interval)))


def _send_telegram_checkpoint_alert(text: str, screenshot_path: Optional[str] = None) -> None:
    """Sends PNG screenshot + alert text to Omar on Telegram when human CAPTCHA/2FA intervention is needed."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594").split(",")[0].strip()
    if screenshot_path and os.path.exists(screenshot_path):
        try:
            boundary = uuid.uuid4().hex
            img_bytes = Path(screenshot_path).read_bytes()
            parts = [
                f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode("utf-8"),
                f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{text[:1000]}\r\n'.encode("utf-8"),
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="checkpoint.png"\r\nContent-Type: image/png\r\n\r\n'.encode("utf-8"),
                img_bytes,
                f"\r\n--{boundary}--\r\n".encode("utf-8"),
            ]
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data=b"".join(parts),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            with urllib.request.urlopen(req, timeout=20):
                return
        except Exception:
            pass
    try:
        from proactive_scheduler import send_telegram
        send_telegram(text)
    except Exception:
        pass


def _resolve_mission_artifact_dir(vault_dir: Optional[str], mission_id: Optional[str]) -> Path:
    raw = vault_dir or os.environ.get("VAULT_DIR") or os.environ.get("OBSIDIAN_VAULT_PATH")
    if raw:
        vpath = Path(os.path.expanduser(raw))
        if vpath.exists():
            mdir = vpath / "missions"
            mdir.mkdir(parents=True, exist_ok=True)
            return mdir
    state_base = Path(os.path.expanduser(os.environ.get("AETHELGARD_STATE_DIR") or "~/.aethelgard/state"))
    mdir = state_base / "portal_artifacts"
    mdir.mkdir(parents=True, exist_ok=True)
    return mdir


def _snapshot_dom_sync(page: Any) -> Dict[str, Any]:
    """Extracts a lightweight accessibility/form snapshot of visible interactive DOM controls."""
    js = """() => {
      const elements = [];
      const nodes = document.querySelectorAll('input, textarea, select, button, a[role="button"]');
      for (let i = 0; i < nodes.length && elements.length < 60; i++) {
        const el = nodes[i];
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        const name = el.getAttribute('name') || '';
        const id = el.getAttribute('id') || '';
        const placeholder = el.getAttribute('placeholder') || '';
        const aria = el.getAttribute('aria-label') || '';
        const txt = (el.innerText || el.value || '').trim().slice(0, 80);
        let selector = tag;
        if (id) selector = '#' + id;
        else if (name) selector = `${tag}[name="${name}"]`;
        else if (type === 'file') selector = 'input[type="file"]';
        else if (aria) selector = `${tag}[aria-label="${aria}"]`;
        elements.push({tag, type, name, id, placeholder, aria, text: txt, selector});
      }
      return {
        title: document.title || '',
        url: location.href,
        text_preview: (document.body ? document.body.innerText : '').slice(0, 3200),
        elements
      };
    }"""
    try:
        raw_snap = page.evaluate(js)
        snap = dict(raw_snap) if isinstance(raw_snap, dict) else {}
    except Exception:
        snap = {}
    if not snap.get("title"):
        try:
            snap["title"] = str(page.title() or "")
        except Exception:
            snap["title"] = ""
    if not snap.get("url"):
        try:
            snap["url"] = str(getattr(page, "url", "") or "")
        except Exception:
            snap["url"] = ""
    if not snap.get("text_preview"):
        try:
            snap["text_preview"] = str(page.inner_text("body") or "")[:3200]
        except Exception:
            snap["text_preview"] = ""
    snap.setdefault("elements", [])
    return snap


def _default_llm_portal_planner(context: Dict[str, Any]) -> Dict[str, Any]:
    """Asks local Antigravity Proxy to output structured JSON actions for the current page state."""
    api_key = os.environ.get("OMNIROUTE_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "sk-omni")
    sys_msg = (
        "You control Playwright to register/log in and complete Omar Elnemr's web portal application.\n"
        "Return strict JSON only: {\"actions\": [...], \"done\": bool, \"summary\": str}.\n"
        "Supported action ops:\n"
        "- {\"op\": \"fill\", \"selector\": str, \"value\": str}  (use \"$PORTAL_PASSWORD\" or \"$OTP_CODE\" placeholders safely)\n"
        "- {\"op\": \"select\", \"selector\": str, \"value\": str}\n"
        "- {\"op\": \"upload\", \"selector\": str, \"kind\": \"cv\"|\"certificates\"}\n"
        "- {\"op\": \"click\", \"selector\": str}\n"
        "- {\"op\": \"save_credential\", \"email\": str, \"username\": str}\n"
        "- {\"op\": \"wait_email_verification\"}\n"
        "- {\"op\": \"done\", \"submitted\": bool, \"confirmation\": str}\n"
        "NEVER attempt to solve or click CAPTCHAs or Turnstile checkboxes."
    )
    payload = {
        "model": PORTAL_LLM_MODEL,
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)[:12000]},
        ],
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        PROXY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    raw = str(data["choices"][0]["message"]["content"] or "{}").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    return json.loads(raw)


def _save_screenshot_safe(page: Any, out_path: Path) -> Optional[str]:
    try:
        page.screenshot(path=str(out_path), full_page=False)
        return str(out_path)
    except Exception:
        return None


def run_portal_task(
    url: str,
    instructions: str,
    mission_id: Optional[str] = None,
    auto_submit: bool = True,
    vault_dir: Optional[str] = None,
    playwright_factory: Optional[Callable[[], Any]] = None,
    google_adapter: Any = None,
    llm_planner: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    otp_timeout_sec: float = 90.0,
    max_rounds: int = 8,
) -> Dict[str, Any]:
    """
    Runs an ephemeral Playwright Chromium workflow for `url`:
      - Reuses stored credentials & browser profile if paired
      - Creates new account with Omar's details + encrypted generated password when signup is needed
      - Polls Gmail for verification links / OTP codes cleanly
      - Uploads CV + Etihad certificates
      - Pauses immediately with a Telegram screenshot on CAPTCHA / Turnstile / SMS-2FA
      - Closes Chromium in `finally:` so idle VM memory stays minimal
    """
    domain = _normalize_domain(url)
    artifact_dir = _resolve_mission_artifact_dir(vault_dir, mission_id)
    profile_dir = _get_credentials_dir() / "browser_profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    storage_file = profile_dir / f"{domain}.json"

    import job_application_engine
    assets = job_application_engine.locate_career_assets(vault_dir=vault_dir)
    identity = dict(job_application_engine.OMAR_IDENTITY)
    stored_cred = get_portal_credential(domain)
    portal_password = stored_cred["password"] if stored_cred else generate_strong_password()

    pw_cm = None
    browser = None
    context = None
    try:
        if playwright_factory is not None:
            pw_cm = playwright_factory()
        else:
            from playwright.sync_api import sync_playwright
            pw_cm = sync_playwright()

        pw = pw_cm.__enter__()
        browser = pw.chromium.launch(headless=True, args=LOW_RAM_CHROMIUM_ARGS)
        ctx_kwargs: Dict[str, Any] = {"viewport": {"width": 1280, "height": 800}}
        if storage_file.exists():
            ctx_kwargs["storage_state"] = str(storage_file)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.set_default_timeout(20000)
        page.goto(url, wait_until="domcontentloaded")

        planner = llm_planner or _default_llm_portal_planner
        latest_otp = ""
        actions_taken: List[str] = []

        for _round in range(max_rounds):
            snap = _snapshot_dom_sync(page)
            is_captcha, reason = detect_human_verification_gate(
                title=str(snap.get("title") or ""),
                html_or_text=str(snap.get("text_preview") or ""),
                url=str(snap.get("url") or page.url),
            )
            if is_captcha:
                ss_path = artifact_dir / f"{mission_id or domain}_captcha_{uuid.uuid4().hex[:6]}.png"
                saved_ss = _save_screenshot_safe(page, ss_path)
                alert = (
                    f"⏸ *Human Verification Needed (`{domain}`)*\n"
                    f"• *Reason:* {reason}\n"
                    f"• *URL:* `{snap.get('url') or url}`\n"
                    + (f"• *Resume Mission:* `/resume {mission_id}` after completing check." if mission_id else "")
                )
                _send_telegram_checkpoint_alert(alert, screenshot_path=saved_ss)
                return {
                    "success": False,
                    "status": "waiting_human",
                    "reason": reason,
                    "url": str(snap.get("url") or url),
                    "screenshot_path": saved_ss or "",
                    "actions_taken": actions_taken,
                }

            plan = planner({
                "round": _round + 1,
                "goal_instructions": instructions,
                "auto_submit": auto_submit,
                "portal_domain": domain,
                "has_existing_account": bool(stored_cred),
                "account_email": (stored_cred or {}).get("email") or identity.get("email"),
                "has_otp_code": bool(latest_otp),
                "identity": {
                    "name": identity.get("name"),
                    "email": identity.get("email"),
                    "phone": identity.get("phone"),
                    "location": identity.get("location"),
                    "linkedin": identity.get("linkedin"),
                },
                "available_assets": {
                    "primary_cv": assets.get("primary_cv"),
                    "certificates": assets.get("certificates") or [],
                },
                "page_snapshot": snap,
            })

            round_actions = plan.get("actions") or []
            done_now = bool(plan.get("done"))
            confirmation = str(plan.get("summary") or "")

            for act in round_actions:
                op = str(act.get("op") or "").lower().strip()
                sel = str(act.get("selector") or "")
                val = str(act.get("value") or "")
                if val == "$PORTAL_PASSWORD":
                    val = portal_password
                elif val == "$OTP_CODE":
                    val = latest_otp

                if op == "fill" and sel:
                    page.fill(sel, val)
                    actions_taken.append(f"fill:{sel}")
                elif op == "select" and sel:
                    page.select_option(sel, val)
                    actions_taken.append(f"select:{sel}")
                elif op == "upload" and sel:
                    kind = str(act.get("kind") or "cv").lower()
                    files_to_up: List[str] = []
                    if kind == "certificates":
                        files_to_up = [p for p in (assets.get("certificates") or [])[:5] if p]
                    elif assets.get("primary_cv"):
                        files_to_up = [assets["primary_cv"]]
                    if files_to_up:
                        page.set_input_files(sel, files_to_up)
                        actions_taken.append(f"upload:{kind}:{len(files_to_up)}")
                elif op == "click" and sel:
                    page.click(sel)
                    actions_taken.append(f"click:{sel}")
                elif op == "save_credential":
                    cred_email = str(act.get("email") or identity.get("email") or "")
                    cred_user = str(act.get("username") or cred_email)
                    save_portal_credential(domain, email=cred_email, password=portal_password, username=cred_user)
                    stored_cred = {"email": cred_email, "username": cred_user, "password": portal_password}
                    actions_taken.append(f"save_credential:{domain}")
                elif op == "wait_email_verification":
                    ver = poll_gmail_verification(
                        domain,
                        timeout_sec=otp_timeout_sec,
                        poll_interval=2.0 if otp_timeout_sec <= 10 else 5.0,
                        google_adapter=google_adapter,
                    )
                    if ver.get("found"):
                        if ver.get("verification_url"):
                            page.goto(ver["verification_url"], wait_until="domcontentloaded")
                            actions_taken.append("email_verify_link")
                        if ver.get("otp_code"):
                            latest_otp = ver["otp_code"]
                            actions_taken.append("email_otp_resolved")
                    else:
                        return {
                            "success": False,
                            "status": "waiting_human",
                            "reason": ver.get("error") or "Timed out waiting for verification email in Gmail",
                            "url": str(page.url),
                            "actions_taken": actions_taken,
                        }
                elif op == "done":
                    done_now = True
                    confirmation = str(act.get("confirmation") or confirmation or "Completed portal steps.")
                    break

            if done_now:
                try:
                    context.storage_state(path=str(storage_file))
                except Exception:
                    pass
                ss_done = artifact_dir / f"{mission_id or domain}_done_{uuid.uuid4().hex[:6]}.png"
                saved_done = _save_screenshot_safe(page, ss_done)
                return {
                    "success": True,
                    "status": "completed",
                    "domain": domain,
                    "url": str(getattr(page, "url", url)),
                    "confirmation": confirmation or "Portal workflow executed.",
                    "screenshot_path": saved_done or "",
                    "actions_taken": actions_taken,
                    "credential_saved": bool(stored_cred),
                }

        ss_end = artifact_dir / f"{mission_id or domain}_final_{uuid.uuid4().hex[:6]}.png"
        saved_end = _save_screenshot_safe(page, ss_end)
        return {
            "success": True,
            "status": "completed",
            "domain": domain,
            "url": url,
            "confirmation": f"Executed {len(actions_taken)} browser actions.",
            "screenshot_path": saved_end or "",
            "actions_taken": actions_taken,
            "credential_saved": bool(stored_cred),
        }
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if pw_cm is not None:
            try:
                pw_cm.__exit__(None, None, None)
            except Exception:
                pass
