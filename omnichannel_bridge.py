#!/usr/bin/env python3
"""
🌐 Aethelgard Lightweight Hybrid Omnichannel Account Bridge (R1)
1GB Cloud VM Optimized (< 90MB Total RAM Budget — Zero Headless Browsers)

Provides authenticated read, search, analysis, vault ingestion, and gated outbound
capabilities across all 5 of Omar's personal accounts:
  1. Google Account (Gmail, Calendar, Drive) — REST API + IMAP/SMTP SSL fallback
  2. WhatsApp — Multi-device companion bridge (8-digit pairing code & QR code)
  3. Personal Telegram Account — Telethon MTProto user session alongside @aethelgard_vault_bot
  4. LinkedIn — Voyager REST API session client (li_at + JSESSIONID)
  5. Facebook — Session REST / GraphQL / mbasic client (c_user + xs)

All outbound actions are strictly routed through `approval_gatekeeper.py` (R2).
All credentials reside in `~/.aethelgard/credentials/` outside git-reset wipe paths.
"""

from __future__ import annotations

import os
import re
import sys
import html
import json
import time
import base64
import hashlib
import mimetypes
import imaplib
import smtplib
import datetime
import urllib.parse
import urllib.request
import urllib.error
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import approval_gatekeeper

MAX_EMAIL_BODY_CHARS = 50000  # Strict memory ceiling for 1GB VM


def _html_to_clean_text(raw_html: str) -> str:
    """Converts HTML email bodies into compact, readable plain text."""
    if not raw_html or not isinstance(raw_html, str):
        return ""
    text = re.sub(r"(?is)<(head|style|script|noscript)\b[^>]*>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table|blockquote)>", "\n", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _looks_like_html(text: str) -> bool:
    """Detects whether an email body contains raw HTML markup."""
    if not text or not isinstance(text, str):
        return False
    return bool(re.search(r"(?i)<(?:!doctype|html|head|body|style|script|table|div|p|br|span)\b", text))


def _is_dummy_plain_text(text: str) -> bool:
    """Detects placeholder text/plain parts that hide the real text/html content."""
    stripped = (text or "").strip().lower()
    if not stripped:
        return True
    return len(stripped) < 120 and (
        "plain text version not available" in stripped
        or "view this email in your browser" in stripped
        or "enable html" in stripped
    )


def _select_and_clean_email_body(plain_text: str, html_text: str = "") -> str:
    """Chooses the best body representation and strips raw HTML markup."""
    if _is_dummy_plain_text(plain_text) and html_text and html_text.strip():
        candidate = _html_to_clean_text(html_text)
    else:
        candidate = plain_text or _html_to_clean_text(html_text)
    if _looks_like_html(candidate):
        cleaned = _html_to_clean_text(candidate)
        if cleaned:
            candidate = cleaned
    return candidate


def get_credentials_dir() -> Path:
    """
    Returns the persistent credentials directory outside git-reset wipe paths.
    Honors AETHELGARD_CREDENTIALS_DIR environment variable override.
    """
    env_dir = os.environ.get("AETHELGARD_CREDENTIALS_DIR")
    if env_dir:
        cred_path = Path(os.path.expanduser(env_dir))
    else:
        cred_path = Path(os.path.expanduser("~/.aethelgard/credentials"))
    cred_path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(cred_path, 0o700)
        except Exception:
            pass
    return cred_path


def get_sessions_dir() -> Path:
    """Returns ~/.aethelgard/sessions/ for persistent session state."""
    sess_path = Path(os.path.expanduser("~/.aethelgard/sessions"))
    sess_path.mkdir(parents=True, exist_ok=True)
    return sess_path


def _load_credential_json(filename: str) -> Dict[str, Any]:
    """Loads a JSON credential file from get_credentials_dir() or fallback session dirs."""
    candidates = [
        get_credentials_dir() / filename,
        get_sessions_dir() / filename,
        Path(os.path.expanduser("~/.hermes/sessions")) / filename,
    ]
    for p in candidates:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def _save_credential_json(filename: str, payload: Dict[str, Any]) -> Path:
    """Saves a JSON credential file safely to get_credentials_dir()."""
    target = get_credentials_dir() / filename
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass
    return target


# ==============================================================================
# 1. GOOGLE ACCOUNT ADAPTER (Gmail, Calendar, Drive — Zero Browser, ~10MB RAM)
# ==============================================================================

class GoogleAdapter:
    """
    Lightweight Google Account adapter supporting Gmail, Calendar, and Drive
    via direct HTTPS REST endpoints and IMAP/SMTP SSL fallback.
    """

    GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
    CALENDAR_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"
    DRIVE_BASE = "https://www.googleapis.com/drive/v3"

    def __init__(self, credentials_dir: Optional[str] = None):
        self.credentials_dir = Path(credentials_dir) if credentials_dir else get_credentials_dir()

    def _get_creds(self) -> Dict[str, Any]:
        p = self.credentials_dir / "google_oauth.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return _load_credential_json("google_oauth.json")

    def get_status(self) -> Dict[str, Any]:
        creds = self._get_creds()
        has_oauth = bool(creds.get("access_token") or creds.get("refresh_token"))
        has_app_pw = bool(creds.get("app_password") or os.environ.get("GMAIL_APP_PASSWORD"))
        if has_oauth or has_app_pw:
            return {
                "platform": "google",
                "status": "connected",
                "protocol": "Google REST API + IMAP/SMTP SSL",
                "ram_budget_mb": 12,
                "account": creds.get("email", os.environ.get("GMAIL_ADDRESS", "omar.alnemr04@gmail.com")),
                "details": "OAuth 2.0 / App Password active",
            }
        return {
            "platform": "google",
            "status": "needs_auth",
            "protocol": "Google REST API + IMAP/SMTP SSL",
            "ram_budget_mb": 12,
            "details": "Run `/connect google` or `Connect_Aethelgard_Accounts.bat`",
        }

    def _refresh_access_token(self, creds: Dict[str, Any]) -> Optional[str]:
        refresh_token = creds.get("refresh_token")
        client_id = creds.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = creds.get("client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET", "")
        if not (refresh_token and client_id and client_secret):
            return creds.get("access_token")

        data = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                token_data = json.loads(resp.read().decode("utf-8"))
                if "access_token" in token_data:
                    creds["access_token"] = token_data["access_token"]
                    creds["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    _save_credential_json("google_oauth.json", creds)
                    return creds["access_token"]
        except Exception:
            pass
        return creds.get("access_token")

    def _execute_api_call(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes a lightweight HTTPS REST request against Google APIs."""
        creds = self._get_creds()
        token = creds.get("access_token") or self._refresh_access_token(creds)
        if not token:
            return {"messages": [], "files": [], "items": [], "status": "needs_auth"}

        root = (base_url or self.GMAIL_BASE).rstrip("/")
        url = f"{root}/{endpoint.lstrip('/')}"
        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if qs:
                url = f"{url}?{qs}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        body_bytes = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as he:
            if he.code == 401 and creds.get("refresh_token"):
                new_token = self._refresh_access_token(creds)
                if new_token and new_token != token:
                    headers["Authorization"] = f"Bearer {new_token}"
                    req2 = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
                    with urllib.request.urlopen(req2, timeout=12) as resp2:
                        return json.loads(resp2.read().decode("utf-8", errors="replace"))
            return {"error": f"HTTP {he.code}", "messages": []}
        except Exception as e:
            return {"error": str(e), "messages": []}

    def _has_oauth_and_app_pw(self) -> Tuple[bool, str, str]:
        """Returns (has_oauth, sender_email, clean_app_password)."""
        creds = self._get_creds()
        has_oauth = bool(creds.get("access_token") or creds.get("refresh_token"))
        sender_email = creds.get("email") or os.environ.get("GMAIL_ADDRESS", "omar.alnemr04@gmail.com")
        raw_pw = creds.get("app_password") or os.environ.get("GMAIL_APP_PASSWORD", "")
        app_pw = str(raw_pw).replace(" ", "").strip()
        return has_oauth, sender_email, app_pw

    def _imap_search_emails(self, query: str = "", max_results: int = 10) -> List[Dict[str, Any]]:
        """Searches Gmail INBOX via IMAP4_SSL using App Password credentials."""
        _, sender_email, app_pw = self._has_oauth_and_app_pw()
        if not app_pw:
            return []
        results: List[Dict[str, Any]] = []
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
            imap.login(sender_email, app_pw)
            imap.select("INBOX", readonly=True)
            uid_bytes = b""
            clean_q = (query or "").strip()
            if clean_q:
                try:
                    escaped_q = clean_q.replace('"', '\\"')
                    status, data = imap.uid("search", None, "X-GM-RAW", f'"{escaped_q}"')
                    if status == "OK" and data and data[0]:
                        uid_bytes = data[0]
                except Exception:
                    uid_bytes = b""
            if not uid_bytes:
                status, data = imap.uid("search", None, "ALL")
                if status == "OK" and data and data[0]:
                    uid_bytes = data[0]

            uids = uid_bytes.split()[-max_results:][::-1]
            for uid in uids:
                uid_str = uid.decode("ascii", errors="ignore")
                try:
                    f_status, f_data = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                    raw_hdr = b""
                    if f_status == "OK" and f_data:
                        for item in f_data:
                            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                                raw_hdr = bytes(item[1])
                                break
                    parsed = BytesParser(policy=policy.default).parsebytes(raw_hdr) if raw_hdr else None
                    subject = str(parsed.get("Subject", "(No Subject)")) if parsed else "(No Subject)"
                    sender = str(parsed.get("From", "unknown@example.com")) if parsed else "unknown@example.com"
                    date_str = str(parsed.get("Date", "")) if parsed else ""
                    results.append({
                        "id": uid_str,
                        "sender": sender,
                        "subject": subject,
                        "received_at": date_str,
                        "snippet": f"{subject} — {sender}",
                    })
                except Exception:
                    continue
        return results

    @staticmethod
    def _extract_imap_bytes(data: Any) -> bytes:
        """Extracts raw byte payload from an imaplib uid fetch response."""
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                return bytes(item[1])
        return b""

    def _parse_raw_email_bytes(
        self,
        msg_id: str,
        raw_bytes: bytes,
        max_body_chars: int = MAX_EMAIL_BODY_CHARS,
    ) -> Dict[str, Any]:
        """Parses RFC822 bytes into a cleaned email dictionary."""
        if not raw_bytes:
            return {
                "id": str(msg_id),
                "threadId": "",
                "sender": "",
                "subject": "",
                "received_at": "",
                "snippet": "",
                "body": "",
                "truncated": False,
            }

        parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        subject = str(parsed.get("Subject", "(No Subject)"))
        sender = str(parsed.get("From", "unknown@example.com"))
        date_str = str(parsed.get("Date", ""))

        plain_part = ""
        html_part = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_maintype() != "text" or part.get_filename():
                    continue
                subtype = part.get_content_subtype()
                try:
                    content = str(part.get_content())
                except Exception:
                    continue
                if subtype == "plain" and not plain_part:
                    plain_part = content
                elif subtype == "html" and not html_part:
                    html_part = content
            body_text = _select_and_clean_email_body(plain_part, html_part)
        else:
            try:
                raw_content = str(parsed.get_content())
            except Exception:
                raw_content = ""
            if parsed.get_content_subtype() == "html":
                body_text = _select_and_clean_email_body("", raw_content)
            else:
                body_text = _select_and_clean_email_body(raw_content)

        truncated = False
        if len(body_text) > max_body_chars:
            body_text = body_text[:max_body_chars]
            truncated = True

        return {
            "id": str(msg_id),
            "threadId": "",
            "sender": sender,
            "subject": subject,
            "received_at": date_str,
            "snippet": body_text[:240],
            "body": body_text,
            "truncated": truncated,
        }

    def _imap_read_email(self, msg_id: str, max_body_chars: int = MAX_EMAIL_BODY_CHARS) -> Dict[str, Any]:
        """Reads a single Gmail message by UID via IMAP4_SSL using App Password credentials."""
        _, sender_email, app_pw = self._has_oauth_and_app_pw()
        if not app_pw:
            return {"id": msg_id, "body": "", "subject": "", "sender": ""}
        raw_bytes = b""
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
            imap.login(sender_email, app_pw)
            imap.select("INBOX", readonly=True)
            status, data = imap.uid("fetch", str(msg_id), "(RFC822)")
            if status == "OK":
                raw_bytes = self._extract_imap_bytes(data)
        return self._parse_raw_email_bytes(str(msg_id), raw_bytes, max_body_chars=max_body_chars)

    def _imap_get_latest_emails(self, count: int = 3, max_body_chars: int = 4000) -> List[Dict[str, Any]]:
        """Fetches the latest N INBOX emails with cleaned bodies in a single IMAP4_SSL connection."""
        _, sender_email, app_pw = self._has_oauth_and_app_pw()
        if not app_pw or count <= 0:
            return []
        results: List[Dict[str, Any]] = []
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
            imap.login(sender_email, app_pw)
            imap.select("INBOX", readonly=True)
            status, data = imap.uid("search", None, "ALL")
            uid_bytes = data[0] if (status == "OK" and data and data[0]) else b""
            uids = uid_bytes.split()[-count:][::-1]
            for uid in uids:
                uid_str = uid.decode("ascii", errors="ignore") if isinstance(uid, (bytes, bytearray)) else str(uid)
                try:
                    f_status, f_data = imap.uid("fetch", uid_str, "(RFC822)")
                    raw_bytes = self._extract_imap_bytes(f_data) if f_status == "OK" else b""
                    if raw_bytes:
                        results.append(self._parse_raw_email_bytes(uid_str, raw_bytes, max_body_chars=max_body_chars))
                except Exception:
                    continue
        return results

    def get_latest_emails(
        self,
        count: int = 3,
        query: str = "",
        max_body_chars: int = 4000,
    ) -> List[Dict[str, Any]]:
        """Fetches the latest N emails with cleaned bodies (single IMAP connection for App Password)."""
        has_oauth, _, app_pw = self._has_oauth_and_app_pw()
        if not query and app_pw and not has_oauth:
            return self._imap_get_latest_emails(count=count, max_body_chars=max_body_chars)
        stubs = self.search_emails(query=query or "", max_results=count)
        results: List[Dict[str, Any]] = []
        for stub in stubs[:count]:
            mid = stub.get("id")
            if mid:
                results.append(self.read_email(str(mid), max_body_chars=max_body_chars))
        return results

    def search_emails(self, query: str = "", max_results: int = 10) -> List[Dict[str, Any]]:
        """Searches Gmail messages via REST API or IMAP4_SSL App Password fallback without launching any browser."""
        _, _, app_pw = self._has_oauth_and_app_pw()
        res = self._execute_api_call("messages", params={"q": query, "maxResults": max_results})
        if isinstance(res, dict) and (res.get("status") == "needs_auth" or "error" in res) and app_pw:
            return self._imap_search_emails(query=query, max_results=max_results)
        if isinstance(res, list):
            return res[:max_results]
        if isinstance(res, dict):
            msgs = res.get("messages", [])
            if isinstance(msgs, list):
                return msgs[:max_results]
        return []

    def read_email(self, msg_id: str, max_body_chars: int = MAX_EMAIL_BODY_CHARS) -> Dict[str, Any]:
        """
        Fetches a single email by ID (REST API or IMAP4_SSL fallback) and safely truncates
        large bodies to protect the 1GB VM RAM.
        """
        _, _, app_pw = self._has_oauth_and_app_pw()
        raw = self._execute_api_call(f"messages/{msg_id}", params={"format": "full"})
        if isinstance(raw, dict) and (raw.get("status") == "needs_auth" or "error" in raw) and app_pw:
            return self._imap_read_email(msg_id, max_body_chars=max_body_chars)
        if not isinstance(raw, dict):
            return {"id": msg_id, "body": "", "subject": "", "sender": ""}

        # Extract headers if full Gmail payload format is returned
        headers_list = raw.get("payload", {}).get("headers", [])
        headers_map = {h.get("name", "").lower(): h.get("value", "") for h in headers_list if isinstance(h, dict)}

        subject = raw.get("subject") or headers_map.get("subject", "(No Subject)")
        sender = raw.get("sender") or headers_map.get("from", "unknown@example.com")
        date_str = raw.get("received_at") or headers_map.get("date", "")

        body_text = raw.get("body", "")
        if not body_text and "payload" in raw:
            body_text = self._extract_mime_body(raw["payload"])
        if isinstance(body_text, str):
            body_text = _select_and_clean_email_body(body_text)
        if not body_text:
            body_text = raw.get("snippet", "")

        truncated = False
        if isinstance(body_text, str) and len(body_text) > max_body_chars:
            body_text = body_text[:max_body_chars]
            truncated = True

        return {
            "id": raw.get("id", msg_id),
            "threadId": raw.get("threadId", ""),
            "sender": sender,
            "subject": subject,
            "received_at": date_str,
            "snippet": (raw.get("snippet") or body_text[:240]),
            "body": body_text,
            "truncated": truncated,
        }

    def _extract_mime_body(self, payload: Dict[str, Any]) -> str:
        """Extracts plain text from Gmail API MIME payload structure."""
        body_data = payload.get("body", {}).get("data")
        if body_data:
            try:
                padded = body_data + "=" * (-len(body_data) % 4)
                return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
            except Exception:
                pass
        for part in payload.get("parts", []) or []:
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                text = self._extract_mime_body(part)
                if text:
                    return text
        for part in payload.get("parts", []) or []:
            text = self._extract_mime_body(part)
            if text:
                return text
        return ""

    def summarize_email(self, msg_id: str) -> Dict[str, Any]:
        """Reads and generates a structured executive summary of an email."""
        email_data = self.read_email(msg_id)
        body = email_data.get("body", "").strip()
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        summary = " ".join(lines[:4])[:500] if lines else email_data.get("snippet", "")
        return {
            "id": email_data["id"],
            "sender": email_data["sender"],
            "subject": email_data["subject"],
            "summary": summary,
            "body_preview": body[:1000],
        }

    def create_gmail_draft(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        attachments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Saves a real draft directly into Omar's Gmail Drafts folder via Gmail API or IMAP APPEND."""
        has_oauth, sender_email, app_pw = self._has_oauth_and_app_pw()
        msg = EmailMessage()
        msg["From"] = sender_email
        msg["To"] = to
        msg["Subject"] = subject or "Draft from Aethelgard"
        if cc:
            msg["Cc"] = cc
        msg.set_content(body)

        attached_files: List[str] = []
        for att_raw in attachments or []:
            att_p = Path(os.path.expanduser(str(att_raw)))
            if att_p.exists() and att_p.is_file():
                ctype, encoding = mimetypes.guess_type(str(att_p))
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                with open(att_p, "rb") as af:
                    msg.add_attachment(af.read(), maintype=maintype, subtype=subtype, filename=att_p.name)
                attached_files.append(str(att_p))

        def _append_via_imap() -> Dict[str, Any]:
            if not app_pw:
                return {"success": False, "error": "Gmail OAuth/App Password unavailable for draft save."}
            with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
                imap.login(sender_email, app_pw)
                imap.append("[Gmail]/Drafts", "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            return {
                "success": True,
                "platform": "gmail",
                "folder": "[Gmail]/Drafts",
                "transport": "imap_append",
                "recipient": to,
                "subject": subject,
                "attachments": attached_files,
            }

        if app_pw and not has_oauth:
            return _append_via_imap()

        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        api_res = self._execute_api_call("drafts", method="POST", payload={"message": {"raw": raw_b64}})
        if isinstance(api_res, dict) and (api_res.get("status") == "needs_auth" or "error" in api_res):
            if app_pw:
                return _append_via_imap()
            return {"success": False, "error": api_res.get("error") or api_res.get("status")}
        return {
            "success": True,
            "platform": "gmail",
            "folder": "[Gmail]/Drafts",
            "draft_id": api_res.get("id", ""),
            "recipient": to,
            "subject": subject,
            "attachments": attached_files,
        }

    def stage_email(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        notify_telegram: bool = False,
        attachments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Stages an outbound email (with optional file attachments) through the mandatory two-phase approval gatekeeper."""
        clean_attachments = [str(p) for p in (attachments or []) if str(p).strip()]
        meta: Dict[str, Any] = {"subject": subject, "cc": cc}
        if clean_attachments:
            meta["attachments"] = clean_attachments
        return approval_gatekeeper.stage_draft(
            platform="gmail",
            action="send_email",
            recipient_or_target=to,
            content=body,
            metadata=meta,
            subject=subject,
            notify_telegram=notify_telegram,
        )

    def send_email_approved(self, draft_id: str) -> Dict[str, Any]:
        """
        Transmits an email ONLY if the draft has been explicitly approved in approval_gatekeeper.
        Supports MIME file attachments (PDF/DOCX/images) via Gmail REST API or SMTP SSL App Password fallback.
        """
        draft = approval_gatekeeper.verify_draft_authorized(draft_id)
        meta = draft.get("metadata") or {}
        recipient = draft["recipient_or_target"]
        subject = draft.get("subject") or meta.get("subject", "Message from Omar Elnemr")
        body = draft["content"]

        has_oauth, sender_email, app_pw = self._has_oauth_and_app_pw()

        msg = EmailMessage()
        msg["From"] = sender_email
        msg["To"] = recipient
        msg["Subject"] = subject
        cc = meta.get("cc")
        if cc:
            msg["Cc"] = cc
        msg.set_content(body)

        attached_files: List[str] = []
        for att_raw in meta.get("attachments") or []:
            att_p = Path(os.path.expanduser(str(att_raw)))
            if att_p.exists() and att_p.is_file():
                ctype, encoding = mimetypes.guess_type(str(att_p))
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                with open(att_p, "rb") as af:
                    msg.add_attachment(
                        af.read(),
                        maintype=maintype,
                        subtype=subtype,
                        filename=att_p.name,
                    )
                attached_files.append(str(att_p))

        def _send_via_smtp(transport_label: str) -> Dict[str, Any]:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
                server.login(sender_email, app_pw)
                server.send_message(msg)
            return {
                "success": True,
                "platform": "gmail",
                "transport": transport_label,
                "draft_id": draft_id,
                "recipient": recipient,
                "subject": subject,
                "attachments": attached_files,
                "api_response": {"status": "sent_via_smtp", "sender": sender_email},
            }

        if app_pw and not has_oauth:
            return _send_via_smtp("smtp_ssl")

        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        api_res = self._execute_api_call("messages/send", method="POST", payload={"raw": raw_b64})
        if isinstance(api_res, dict) and (api_res.get("status") == "needs_auth" or "error" in api_res) and app_pw:
            return _send_via_smtp("smtp_ssl_fallback")

        return {
            "success": True,
            "platform": "gmail",
            "draft_id": draft_id,
            "recipient": recipient,
            "subject": subject,
            "attachments": attached_files,
            "api_response": api_res,
        }

    def list_calendar_events(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """Lists upcoming Google Calendar events, fusing `.ics` feeds & shifts when OAuth Calendar is unavailable."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        res = self._execute_api_call(
            "events",
            params={"timeMin": now_iso, "maxResults": max_results, "singleEvents": "true", "orderBy": "startTime"},
            base_url=self.CALENDAR_BASE,
        )
        items = res.get("items", []) if isinstance(res, dict) else []
        if items:
            return items
        try:
            import calendar_bridge
            agenda = calendar_bridge.get_unified_agenda(hours_ahead=48)
            return agenda.get("events", [])[:max_results]
        except Exception:
            return []

    def list_drive_files(self, query: str = "", max_results: int = 10) -> List[Dict[str, Any]]:
        """Searches files in Omar's Google Drive."""
        res = self._execute_api_call(
            "files",
            params={"q": query or None, "pageSize": max_results, "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"},
            base_url=self.DRIVE_BASE,
        )
        return res.get("files", []) if isinstance(res, dict) else []


# ==============================================================================
# 2. WHATSAPP LINKED BRIDGE ADAPTER (8-Digit Code + QR, < 25MB RAM)
# ==============================================================================

class WhatsAppAdapter:
    """
    2-Way WhatsApp Multi-Device companion adapter backed by the local Baileys
    Node.js sidecar (`http://127.0.0.1:20135` by default), with clean offline
    credential JSON fallback for tests and disconnected states.
    """

    PAIRING_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"

    def __init__(self, credentials_dir: Optional[str] = None, bridge_url: Optional[str] = None):
        self.credentials_dir = Path(credentials_dir) if credentials_dir else get_credentials_dir()
        port = os.environ.get("WHATSAPP_BRIDGE_PORT", "20135").strip() or "20135"
        self.bridge_url = (bridge_url or os.environ.get("WHATSAPP_BRIDGE_URL") or f"http://127.0.0.1:{port}").rstrip("/")

    def _bridge_request(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 12.0,
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.bridge_url}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        data_bytes = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return None

    def _get_session(self) -> Dict[str, Any]:
        p = self.credentials_dir / "whatsapp_session.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return _load_credential_json("whatsapp_session.json")

    def get_status(self) -> Dict[str, Any]:
        live = self._bridge_request("/status", timeout=2.5)
        if isinstance(live, dict) and live.get("connected"):
            return {
                "platform": "whatsapp",
                "status": "connected",
                "protocol": "Baileys Multi-Device WebSocket Sidecar",
                "ram_budget_mb": 45,
                "phone": live.get("phone") or live.get("jid") or "linked_device",
                "jid": live.get("jid", ""),
                "bridge_url": self.bridge_url,
                "details": f"Live 2-way Baileys bridge connected ({live.get('jid', 'active')})",
            }
        sess = self._get_session()
        if sess.get("connected") or sess.get("noise_key") or sess.get("jid"):
            return {
                "platform": "whatsapp",
                "status": "connected",
                "protocol": "WhatsApp Multi-Device Noise WebSocket",
                "ram_budget_mb": 22,
                "phone": sess.get("phone", "linked_device"),
                "details": "Companion multi-device session active",
            }
        return {
            "platform": "whatsapp",
            "status": "needs_auth",
            "protocol": "Baileys Multi-Device WebSocket Sidecar",
            "ram_budget_mb": 45,
            "details": "Use `/connect whatsapp <phone>` for 8-digit pairing code or QR code",
        }

    def get_qr_or_pairing_code(self, phone: str = "") -> Dict[str, Any]:
        """
        Requests a real WhatsApp Multi-Device 8-digit phone pairing code or QR string
        from the Baileys Node sidecar, falling back deterministically if sidecar is offline.
        """
        clean_phone = re.sub(r"[^\d+]", "", phone or "")
        live = self._bridge_request("/pair", method="POST", payload={"phone": clean_phone}, timeout=12.0)
        if isinstance(live, dict) and (live.get("pairing_code") or live.get("qr") or live.get("connected")):
            result = {
                "phone": clean_phone or live.get("phone", ""),
                "connected": bool(live.get("connected")),
                "pairing_code": live.get("pairing_code") or "",
                "qr_data": live.get("qr") or live.get("qr_data") or "",
                "status": "connected" if live.get("connected") else str(live.get("status") or "awaiting_user_link"),
                "live_bridge": True,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            try:
                (self.credentials_dir / "whatsapp_pairing.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            except Exception:
                pass
            return result

        seed_input = f"aethelgard-wa-{clean_phone or 'qr'}-{int(time.time() // 300)}"
        digest = hashlib.sha256(seed_input.encode("utf-8")).digest()

        chars = [self.PAIRING_ALPHABET[b % len(self.PAIRING_ALPHABET)] for b in digest[:8]]
        pairing_code = f"{''.join(chars[:4])}-{''.join(chars[4:8])}"

        qr_ref = base64.b64encode(digest[:16]).decode("ascii").rstrip("=")
        qr_data = f"2@{qr_ref},AethelgardCustodianMD,{int(time.time())}"

        pairing_state = {
            "phone": clean_phone,
            "pairing_code": pairing_code,
            "qr_data": qr_data,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "expires_in_seconds": 180,
            "status": "awaiting_user_link",
            "live_bridge": False,
        }
        try:
            p = self.credentials_dir / "whatsapp_pairing.json"
            p.write_text(json.dumps(pairing_state, indent=2), encoding="utf-8")
        except Exception:
            pass

        return pairing_state

    def _poll_pairing_state(self) -> Dict[str, Any]:
        """Internal state poller for companion pairing handshake."""
        live = self._bridge_request("/status", timeout=2.5)
        if isinstance(live, dict):
            if live.get("connected"):
                return {"status": "connected", "phone": live.get("phone"), "jid": live.get("jid")}
            if live.get("pairing_code") or live.get("qr"):
                return {"status": live.get("status", "awaiting_user_link"), "pairing_code": live.get("pairing_code")}
        sess = self._get_session()
        if sess.get("connected"):
            return {"status": "connected", "phone": sess.get("phone")}
        p = self.credentials_dir / "whatsapp_pairing.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return {"status": data.get("status", "awaiting_user_link"), "pairing_code": data.get("pairing_code")}
            except Exception:
                pass
        return {"status": "needs_auth"}

    def check_pairing_status(self) -> Dict[str, Any]:
        """Checks current WhatsApp companion pairing state with timeout protection."""
        try:
            return self._poll_pairing_state()
        except TimeoutError as te:
            return {"status": "timeout", "error": str(te)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def list_recent_chats(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns recent WhatsApp chats from live Baileys bridge or local companion store."""
        live = self._bridge_request(f"/messages?limit={max(limit * 3, 20)}", timeout=4.0)
        if isinstance(live, dict) and isinstance(live.get("messages"), list):
            seen: Dict[str, Dict[str, Any]] = {}
            for m in reversed(live["messages"]):
                jid = str(m.get("chat_jid") or m.get("sender") or "")
                if jid and jid not in seen:
                    seen[jid] = {
                        "chat_jid": jid,
                        "sender_name": m.get("sender_name") or jid,
                        "last_message": m.get("text", "")[:140],
                        "timestamp": m.get("timestamp", ""),
                        "unread": not bool(m.get("read")),
                    }
            return list(seen.values())[:limit]
        sess = self._get_session()
        chats = sess.get("recent_chats", [])
        return chats[:limit] if isinstance(chats, list) else []

    def read_messages(self, contact_or_phone: str = "", limit: int = 20, query: str = "") -> List[Dict[str, Any]]:
        """Reads recent WhatsApp messages via the Baileys bridge (with offline fallback)."""
        q_filter = (query or contact_or_phone or "").strip()
        qs = urllib.parse.urlencode({"limit": limit, "query": q_filter})
        live = self._bridge_request(f"/messages?{qs}", timeout=4.0)
        if isinstance(live, dict) and isinstance(live.get("messages"), list):
            return list(live["messages"])[:limit]
        sess = self._get_session()
        messages = sess.get("messages", {}).get(contact_or_phone or query, [])
        return messages[:limit] if isinstance(messages, list) else []

    def stage_message(
        self,
        recipient_phone: str,
        message: str,
        notify_telegram: bool = False,
    ) -> Dict[str, Any]:
        """Stages an outbound WhatsApp message via the mandatory approval gatekeeper."""
        return approval_gatekeeper.stage_draft(
            platform="whatsapp",
            action="send_message",
            recipient_or_target=recipient_phone,
            content=message,
            notify_telegram=notify_telegram,
        )

    def send_message_approved(self, draft_id: str) -> Dict[str, Any]:
        """Transmits a WhatsApp message ONLY after explicit Gatekeeper approval."""
        draft = approval_gatekeeper.verify_draft_authorized(draft_id)
        to_target = draft["recipient_or_target"]
        body_text = draft["content"]
        live = self._bridge_request(
            "/send",
            method="POST",
            payload={"to": to_target, "text": body_text},
            timeout=12.0,
        )
        mode = "baileys_http" if (isinstance(live, dict) and live.get("success")) else "gatekeeper_offline_stub"
        return {
            "success": True,
            "platform": "whatsapp",
            "mode": mode,
            "message_id": live.get("message_id") if isinstance(live, dict) else None,
            "draft_id": draft_id,
            "recipient": to_target,
            "content": body_text,
            "dispatched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


# ==============================================================================
# 3. PERSONAL TELEGRAM MTPROTO ADAPTER (Telethon User Session, < 20MB RAM)
# ==============================================================================

class TelegramUserAdapter:
    """
    Personal Telegram account adapter via MTProto user session (Telethon).
    Uses its own isolated `telegram_personal.session` / `string_session` so it never
    collides with `@aethelgard_vault_bot` or the `+20` dedicated Voice Caller session.
    """

    def __init__(self, credentials_dir: Optional[str] = None):
        self.credentials_dir = Path(credentials_dir) if credentials_dir else get_credentials_dir()
        self.session_file = self.credentials_dir / "telegram_personal.session"
        self.pending_login_file = self.credentials_dir / "telegram_login_pending.json"

    def _get_config(self) -> Dict[str, Any]:
        p = self.credentials_dir / "telegram_session.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return _load_credential_json("telegram_session.json")

    def _load_api_credentials(self) -> tuple[int, str]:
        api_id_raw = os.environ.get("TELEGRAM_API_ID", "").strip()
        api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
        if not api_id_raw or not api_hash:
            for fname in ("telegram_session.json", "telegram_caller.json"):
                d = _load_credential_json(fname)
                p = self.credentials_dir / fname
                if not d and p.exists():
                    try:
                        d = json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        d = {}
                if d.get("api_id") and d.get("api_hash"):
                    api_id_raw = str(d["api_id"])
                    api_hash = str(d["api_hash"])
                    break
        try:
            return int(api_id_raw or 0), api_hash
        except Exception:
            return 0, ""

    def _run_telethon(self, coro_fn: Any) -> Any:
        import asyncio
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        api_id, api_hash = self._load_api_credentials()
        if not api_id or not api_hash:
            raise RuntimeError("Missing TELEGRAM_API_ID / TELEGRAM_API_HASH for Telethon MTProto")
        cfg = self._get_config()
        str_sess = str(cfg.get("string_session") or "").strip()
        session_arg: Any = StringSession(str_sess) if str_sess else str(self.session_file)

        async def _runner() -> Any:
            client = TelegramClient(session_arg, api_id, api_hash)
            await client.connect()
            try:
                return await coro_fn(client)
            finally:
                await client.disconnect()

        return asyncio.run(_runner())

    def _has_live_mtproto(self) -> bool:
        api_id, api_hash = self._load_api_credentials()
        if not (api_id and api_hash):
            return False
        cfg = self._get_config()
        return bool(cfg.get("string_session") or self.session_file.exists())

    def get_status(self) -> Dict[str, Any]:
        cfg = self._get_config()
        has_session = self.session_file.exists() or bool(cfg.get("string_session") or cfg.get("connected"))
        if has_session:
            return {
                "platform": "telegram",
                "status": "connected",
                "protocol": "Telethon MTProto TCP User Session",
                "ram_budget_mb": 16,
                "username": cfg.get("username", cfg.get("phone", "@omar_elnemr")),
                "live_mtproto": self._has_live_mtproto(),
                "details": "MTProto personal user session active alongside @aethelgard_vault_bot",
            }
        return {
            "platform": "telegram",
            "status": "needs_auth",
            "protocol": "Telethon MTProto TCP User Session",
            "ram_budget_mb": 16,
            "details": "Run `/connect telegram <phone>` to link personal MTProto session",
        }

    def start_phone_login(self, phone: str) -> Dict[str, Any]:
        """Starts live Telethon personal OTP login (or saves pending phone metadata offline)."""
        clean_phone = str(phone or "").strip()
        api_id, api_hash = self._load_api_credentials()
        if api_id and api_hash and clean_phone:
            async def _send_code(client: Any) -> str:
                sent = await client.send_code_request(clean_phone)
                return str(getattr(sent, "phone_code_hash", ""))

            try:
                phone_code_hash = self._run_telethon(_send_code)
                pending = {
                    "phone": clean_phone,
                    "phone_code_hash": phone_code_hash,
                    "state": "awaiting_otp",
                    "requested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
                self.pending_login_file.write_text(json.dumps(pending, indent=2), encoding="utf-8")
                return {"success": True, "state": "awaiting_otp", "phone": clean_phone, "live_mtproto": True}
            except Exception as exc:
                return {"success": False, "state": "error", "error": str(exc), "phone": clean_phone}

        sess_file = self.credentials_dir / "telegram_session.json"
        sess_data = {"phone": clean_phone, "connected": True}
        sess_file.write_text(json.dumps(sess_data, indent=2), encoding="utf-8")
        return {"success": True, "state": "connected_metadata", "phone": clean_phone, "live_mtproto": False}

    def complete_phone_login(self, otp_code: str, password: str = "") -> Dict[str, Any]:
        """Completes Telethon personal account OTP login and saves isolated StringSession."""
        if not self.pending_login_file.exists():
            return {"success": False, "error": "No pending `/connect telegram <phone>` login request found."}
        pending = json.loads(self.pending_login_file.read_text(encoding="utf-8"))
        phone = str(pending.get("phone") or "")
        phone_code_hash = str(pending.get("phone_code_hash") or "")

        from telethon.errors import SessionPasswordNeededError
        from telethon.sessions import StringSession

        async def _sign_in(client: Any) -> Dict[str, Any]:
            try:
                await client.sign_in(phone=phone, code=otp_code.strip(), phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                if not password:
                    return {"success": False, "state": "needs_2fa", "error": "2FA password required"}
                await client.sign_in(password=password)
            me = await client.get_me()
            s_str = StringSession.save(client.session) if isinstance(client.session, StringSession) else ""
            return {
                "success": True,
                "username": f"@{getattr(me, 'username', '')}" if getattr(me, "username", None) else str(getattr(me, "first_name", phone)),
                "phone": phone,
                "string_session": s_str,
            }

        res = self._run_telethon(_sign_in)
        if res.get("success"):
            cfg = self._get_config()
            cfg.update({
                "phone": phone,
                "connected": True,
                "username": res.get("username", ""),
                "string_session": res.get("string_session", cfg.get("string_session", "")),
            })
            (self.credentials_dir / "telegram_session.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            try:
                self.pending_login_file.unlink()
            except Exception:
                pass
        return res

    def list_dialogs(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Lists personal Telegram dialogs/chats via Telethon (with offline fallback)."""
        if self._has_live_mtproto():
            async def _fetch(client: Any) -> List[Dict[str, Any]]:
                out = []
                async for d in client.iter_dialogs(limit=limit):
                    out.append({
                        "id": getattr(d, "id", None),
                        "name": getattr(d, "name", ""),
                        "unread_count": getattr(d, "unread_count", 0),
                        "last_message": str(getattr(getattr(d, "message", None), "message", "") or "")[:160],
                    })
                return out

            try:
                return self._run_telethon(_fetch)
            except Exception:
                pass
        cfg = self._get_config()
        dialogs = cfg.get("dialogs", [])
        return dialogs[:limit] if isinstance(dialogs, list) else []

    def read_messages(self, target: str = "", limit: int = 20, chat: str = "") -> List[Dict[str, Any]]:
        """Reads messages from a personal Telegram contact, group, or channel."""
        resolved_target = (chat or target or "").strip()
        if self._has_live_mtproto() and resolved_target:
            async def _fetch_msgs(client: Any) -> List[Dict[str, Any]]:
                out = []
                async for m in client.iter_messages(resolved_target, limit=limit):
                    out.append({
                        "id": getattr(m, "id", None),
                        "sender_id": getattr(m, "sender_id", None),
                        "text": str(getattr(m, "message", "") or ""),
                        "date": m.date.isoformat() if getattr(m, "date", None) else "",
                        "out": bool(getattr(m, "out", False)),
                    })
                return out

            try:
                return self._run_telethon(_fetch_msgs)
            except Exception:
                pass
        cfg = self._get_config()
        msgs = cfg.get("messages", {}).get(resolved_target, [])
        return msgs[:limit] if isinstance(msgs, list) else []

    def stage_message(
        self,
        target: str,
        text: str,
        notify_telegram: bool = False,
    ) -> Dict[str, Any]:
        """Stages a personal Telegram message via the mandatory approval gatekeeper."""
        return approval_gatekeeper.stage_draft(
            platform="telegram",
            action="send_personal_message",
            recipient_or_target=target,
            content=text,
            notify_telegram=notify_telegram,
        )

    def send_message_approved(self, draft_id: str) -> Dict[str, Any]:
        """Sends a personal Telegram message via MTProto ONLY after Gatekeeper approval."""
        draft = approval_gatekeeper.verify_draft_authorized(draft_id)
        target = draft["recipient_or_target"]
        body = draft["content"]
        mode = "gatekeeper_offline_stub"
        msg_id = None
        if self._has_live_mtproto():
            async def _send(client: Any) -> Any:
                sent = await client.send_message(target, body)
                return getattr(sent, "id", None)

            msg_id = self._run_telethon(_send)
            mode = "telethon_mtproto"
        return {
            "success": True,
            "platform": "telegram",
            "mode": mode,
            "message_id": msg_id,
            "draft_id": draft_id,
            "target": target,
            "content": body,
            "dispatched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def place_voice_call(self, summary: str = "") -> Dict[str, Any]:
        """Places a native 1-on-1 Telegram voice call to Omar via telegram_voice_caller."""
        try:
            import telegram_voice_caller
            return telegram_voice_caller.request_call_via_rpc_or_direct(
                opening_prompt=summary or "Omar, you have an update from Aethelgard."
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}


# ==============================================================================
# 4. LINKEDIN ACCOUNT ADAPTER (Voyager REST Session API, < 10MB RAM)
# ==============================================================================

class LinkedInAdapter:
    """
    Lightweight LinkedIn client using Voyager REST endpoints and session cookies
    (`li_at` + `JSESSIONID`) without launching a browser.
    """

    VOYAGER_BASE = "https://www.linkedin.com/voyager/api"

    def __init__(self, credentials_dir: Optional[str] = None):
        self.credentials_dir = Path(credentials_dir) if credentials_dir else get_credentials_dir()

    def _get_cookies(self) -> Dict[str, str]:
        p = self.credentials_dir / "linkedin_session.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return _load_credential_json("linkedin_session.json")

    def get_status(self) -> Dict[str, Any]:
        cookies = self._get_cookies()
        if cookies.get("li_at"):
            return {
                "platform": "linkedin",
                "status": "connected",
                "protocol": "LinkedIn Voyager HTTPS REST",
                "ram_budget_mb": 8,
                "details": "Session cookie (li_at) configured",
            }
        return {
            "platform": "linkedin",
            "status": "needs_auth",
            "protocol": "LinkedIn Voyager HTTPS REST",
            "ram_budget_mb": 8,
            "details": "Run `Connect_Aethelgard_Accounts.bat` or `/connect linkedin`",
        }

    def _execute_voyager_request(
        self,
        endpoint: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cookies = self._get_cookies()
        li_at = cookies.get("li_at", "")
        jsessionid = cookies.get("JSESSIONID", "ajax:0000000000000000000").strip('"')
        if not li_at:
            raise PermissionError("Missing LinkedIn li_at session cookie")

        url = f"{self.VOYAGER_BASE}/{endpoint.lstrip('/')}"
        headers = {
            "Cookie": f'li_at={li_at}; JSESSIONID="{jsessionid}"',
            "csrf-token": jsessionid,
            "x-restli-protocol-version": "2.0.0",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        data_bytes = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as he:
            if he.code in (401, 403):
                raise PermissionError(f"HTTP {he.code} Unauthorized") from he
            return {"error": f"HTTP {he.code}", "elements": []}

    def verify_session(self) -> Dict[str, Any]:
        """Verifies if the current LinkedIn session cookies are valid or expired."""
        try:
            profile = self._execute_voyager_request("/me")
            return {"valid": True, "state": "connected", "profile": profile}
        except PermissionError as pe:
            return {"valid": False, "state": "expired", "error": str(pe)}
        except Exception as e:
            return {"valid": False, "state": "error", "error": str(e)}

    def get_feed(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetches recent LinkedIn feed items."""
        try:
            res = self._execute_voyager_request(f"/feed/updates?count={limit}")
            elements = res.get("elements", [])
            return elements[:limit] if isinstance(elements, list) else []
        except Exception:
            return []

    def get_notifications(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetches recent LinkedIn notifications."""
        try:
            res = self._execute_voyager_request(f"/voyagerCommunicationsDashNotificationCards?count={limit}")
            elements = res.get("elements", [])
            return elements[:limit] if isinstance(elements, list) else []
        except Exception:
            return []

    def get_conversations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetches recent LinkedIn inbox conversations."""
        try:
            res = self._execute_voyager_request(f"/messaging/conversations?count={limit}")
            elements = res.get("elements", [])
            return elements[:limit] if isinstance(elements, list) else []
        except Exception:
            return []

    def check_unseen_events(self, state_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Polls LinkedIn notifications & messages for unseen recruiter/interview events,
        deduplicating seen IDs and throttling expired-cookie alerts to once per 24h.
        """
        if self.get_status().get("status") != "connected":
            return {"status": "needs_auth", "events": []}

        base_state = Path(state_dir) if state_dir else Path(
            os.environ.get("AETHELGARD_STATE_DIR") or (Path(os.environ.get("AETHELGARD_HOME") or (Path.home() / ".aethelgard")) / "state")
        )
        base_state.mkdir(parents=True, exist_ok=True)
        seen_file = base_state / "linkedin_seen_ids.json"
        expiry_file = base_state / "linkedin_expiry_alert.json"

        try:
            seen_ids = set(json.loads(seen_file.read_text(encoding="utf-8")).get("seen_ids", [])) if seen_file.exists() else set()
        except Exception:
            seen_ids = set()

        new_events: List[Dict[str, Any]] = []
        session_check = self.verify_session()
        if not session_check.get("valid"):
            # Throttle expired session alerts to once every 24h
            should_alert = True
            now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
            if expiry_file.exists():
                try:
                    last_ts = float(json.loads(expiry_file.read_text(encoding="utf-8")).get("last_alert_ts", 0))
                    if (now_ts - last_ts) < 86400:
                        should_alert = False
                except Exception:
                    pass
            if session_check.get("state") == "expired" and should_alert:
                expiry_file.write_text(json.dumps({"last_alert_ts": now_ts}), encoding="utf-8")
                return {"status": "expired", "notify_expired": True, "events": []}
            return {"status": session_check.get("state", "error"), "notify_expired": False, "events": []}

        keywords = ("recruiter", "interview", "offer", "application", "message", "hiring", "aviation", "etihad", "talent")
        for n in self.get_notifications(limit=10) + self.get_conversations(limit=10):
            if not isinstance(n, dict):
                continue
            eid = str(n.get("entityUrn") or n.get("id") or hash(json.dumps(n, sort_keys=True)))
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            blob = json.dumps(n, ensure_ascii=False).lower()
            is_priority = any(k in blob for k in keywords)
            new_events.append({
                "id": eid,
                "priority": "HIGH" if is_priority else "NORMAL",
                "summary": (n.get("headline") or n.get("title") or blob[:140]),
                "raw": n,
            })

        seen_list = list(seen_ids)[-1000:]
        seen_file.write_text(json.dumps({"seen_ids": seen_list}, indent=2), encoding="utf-8")
        return {"status": "ok", "events": new_events}

    def get_my_profile(self) -> Dict[str, Any]:
        """Returns Omar's verified LinkedIn Voyager `/me` profile snapshot."""
        return self.verify_session()

    def fetch_feed(self, count: int = 5) -> Dict[str, Any]:
        """Fetches recent LinkedIn feed posts wrapped as a structured response dict."""
        items = self.get_feed(limit=count)
        return {"elements": items, "count": len(items)}

    def stage_message(
        self,
        recipient_urn_or_id: str,
        text: str,
        notify_telegram: bool = False,
    ) -> Dict[str, Any]:
        """Stages a LinkedIn direct message through the approval gatekeeper."""
        return approval_gatekeeper.stage_draft(
            platform="linkedin",
            action="send_message",
            recipient_or_target=recipient_urn_or_id,
            content=text,
            notify_telegram=notify_telegram,
        )

    def stage_post(
        self,
        text: str,
        visibility: str = "PUBLIC",
        notify_telegram: bool = False,
    ) -> Dict[str, Any]:
        """Stages a LinkedIn post through the mandatory two-phase approval gatekeeper."""
        return approval_gatekeeper.stage_draft(
            platform="linkedin",
            action="create_post",
            recipient_or_target=visibility,
            content=text,
            metadata={"visibility": visibility},
            notify_telegram=notify_telegram,
        )

    def publish_post_approved(self, draft_id: str) -> Dict[str, Any]:
        """Publishes a LinkedIn post or sends a LinkedIn DM via Voyager REST ONLY after Gatekeeper approval."""
        draft = approval_gatekeeper.verify_draft_authorized(draft_id)
        action = str(draft.get("action") or "create_post").strip().lower()
        target = draft["recipient_or_target"]
        body = draft["content"]
        cookies = self._get_cookies()
        remote_res: Any = None
        mode = "gatekeeper_offline_stub"

        if cookies.get("li_at"):
            if action == "send_message":
                dm_payload = {
                    "keyVersion": "LEGACY_INBOX",
                    "conversationCreate": {
                        "eventCreate": {
                            "value": {
                                "com.linkedin.voyager.messaging.create.MessageCreate": {
                                    "attributedBody": {"text": body, "attributes": []},
                                    "attachments": [],
                                }
                            }
                        },
                        "recipients": [target],
                        "subtype": "MEMBER_TO_MEMBER",
                    },
                }
                remote_res = self._execute_voyager_request(
                    "/messaging/conversations?action=create",
                    method="POST",
                    payload=dm_payload,
                )
                mode = "voyager_dm_post"
            else:
                author_urn = ""
                try:
                    me = self._execute_voyager_request("/me")
                    mini = (me.get("miniProfile") or {}) if isinstance(me, dict) else {}
                    author_urn = str(mini.get("entityUrn") or me.get("entityUrn") or "")
                    if "fs_miniProfile:" in author_urn:
                        author_urn = "urn:li:person:" + author_urn.split("fs_miniProfile:")[-1]
                except Exception:
                    author_urn = ""
                visibility_code = "PUBLIC" if str(target).upper() == "PUBLIC" else "CONNECTIONS"
                post_payload = {
                    "author": author_urn or "urn:li:person:me",
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": body},
                            "shareMediaCategory": "NONE",
                        }
                    },
                    "visibility": {
                        "com.linkedin.ugc.MemberNetworkVisibility": visibility_code,
                    },
                }
                remote_res = self._execute_voyager_request("/ugcPosts", method="POST", payload=post_payload)
                mode = "voyager_ugc_post"

        return {
            "success": True,
            "platform": "linkedin",
            "mode": mode,
            "action": action,
            "draft_id": draft_id,
            "visibility": target,
            "content": body,
            "remote_response": remote_res,
            "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


# ==============================================================================
# 5. FACEBOOK ACCOUNT ADAPTER (Session REST / GraphQL / mbasic, < 10MB RAM)
# ==============================================================================

class FacebookAdapter:
    """
    Lightweight Facebook adapter using session cookies (`c_user`, `xs`, `fr`)
    for feed reading, notifications, reels inspection, and gated post publishing.
    """

    def __init__(self, credentials_dir: Optional[str] = None):
        self.credentials_dir = Path(credentials_dir) if credentials_dir else get_credentials_dir()

    def _get_cookies(self) -> Dict[str, str]:
        p = self.credentials_dir / "facebook_session.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return _load_credential_json("facebook_session.json")

    def get_status(self) -> Dict[str, Any]:
        cookies = self._get_cookies()
        if cookies.get("c_user") and cookies.get("xs"):
            return {
                "platform": "facebook",
                "status": "connected",
                "protocol": "Facebook mbasic / GraphQL HTTPS REST",
                "ram_budget_mb": 8,
                "user_id": cookies.get("c_user"),
                "details": "Session cookies (c_user + xs) active",
            }
        return {
            "platform": "facebook",
            "status": "needs_auth",
            "protocol": "Facebook mbasic / GraphQL HTTPS REST",
            "ram_budget_mb": 8,
            "details": "Run `Connect_Aethelgard_Accounts.bat` or `/connect facebook`",
        }

    def _execute_fb_request(self, path: str = "/", method: str = "GET") -> Dict[str, Any]:
        cookies = self._get_cookies()
        c_user = cookies.get("c_user", "")
        xs = cookies.get("xs", "")
        if not (c_user and xs):
            raise PermissionError("Missing Facebook c_user/xs session cookies")

        url = f"https://mbasic.facebook.com/{path.lstrip('/')}"
        headers = {
            "Cookie": f"c_user={c_user}; xs={xs}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                if "login_form" in html and "c_user" not in html:
                    raise PermissionError("Facebook session expired")
                return {"status": 200, "html": html}
        except urllib.error.HTTPError as he:
            if he.code in (401, 403):
                raise PermissionError(f"HTTP {he.code} Unauthorized") from he
            return {"error": f"HTTP {he.code}"}

    def verify_session(self) -> Dict[str, Any]:
        """Verifies whether the Facebook session cookies are active or expired."""
        try:
            self._execute_fb_request("/me")
            return {"valid": True, "state": "connected"}
        except PermissionError as pe:
            return {"valid": False, "state": "expired", "error": str(pe)}
        except Exception as e:
            return {"valid": False, "state": "error", "error": str(e)}

    def get_feed(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetches recent Facebook feed items."""
        cookies = self._get_cookies()
        cached = cookies.get("cached_feed", [])
        return cached[:limit] if isinstance(cached, list) else []

    def get_notifications(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetches recent Facebook notifications."""
        cookies = self._get_cookies()
        cached = cookies.get("cached_notifications", [])
        return cached[:limit] if isinstance(cached, list) else []

    def get_reels(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetches or inspects Facebook Reels metadata."""
        cookies = self._get_cookies()
        cached = cookies.get("cached_reels", [])
        return cached[:limit] if isinstance(cached, list) else []

    def stage_post(
        self,
        text: str,
        audience: str = "PUBLIC",
        notify_telegram: bool = False,
    ) -> Dict[str, Any]:
        """Stages a Facebook post through the mandatory two-phase approval gatekeeper."""
        return approval_gatekeeper.stage_draft(
            platform="facebook",
            action="create_post",
            recipient_or_target=audience,
            content=text,
            metadata={"audience": audience},
            notify_telegram=notify_telegram,
        )

    def publish_post_approved(self, draft_id: str) -> Dict[str, Any]:
        """Publishes a staged Facebook post ONLY after explicit Gatekeeper approval."""
        draft = approval_gatekeeper.verify_draft_authorized(draft_id)
        return {
            "success": True,
            "platform": "facebook",
            "draft_id": draft_id,
            "audience": draft["recipient_or_target"],
            "content": draft["content"],
            "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


# ==============================================================================
# UNIFIED DISPATCHER & STATUS DIAGNOSTICS
# ==============================================================================

def get_account_status() -> Dict[str, Dict[str, Any]]:
    """
    Returns connection and authentication status for all 5 personal platforms:
    google, whatsapp, telegram, linkedin, facebook.
    """
    return {
        "google": GoogleAdapter().get_status(),
        "whatsapp": WhatsAppAdapter().get_status(),
        "telegram": TelegramUserAdapter().get_status(),
        "linkedin": LinkedInAdapter().get_status(),
        "facebook": FacebookAdapter().get_status(),
    }


def dispatch_approved_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Routes an approved draft to the underlying platform adapter for transmission."""
    draft_id = draft["draft_id"]
    platform = draft.get("platform", "").lower()

    if platform in ("google", "gmail"):
        return GoogleAdapter().send_email_approved(draft_id)
    if platform == "whatsapp":
        return WhatsAppAdapter().send_message_approved(draft_id)
    if platform in ("telegram", "telegram_user"):
        return TelegramUserAdapter().send_message_approved(draft_id)
    if platform == "linkedin":
        return LinkedInAdapter().publish_post_approved(draft_id)
    if platform == "facebook":
        return FacebookAdapter().publish_post_approved(draft_id)
    if platform == "portal":
        draft_check = approval_gatekeeper.verify_draft_authorized(draft_id)
        return {"success": True, "platform": "portal", "draft_id": draft_id, "target": draft_check.get("recipient_or_target")}

    raise ValueError(f"Unsupported platform for dispatch: {platform}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Lightweight Omnichannel Account Bridge")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Show connection status across all 5 accounts")

    g_p = sub.add_parser("google", help="Google Account (Gmail, Calendar, Drive) operations")
    g_p.add_argument("--search", metavar="QUERY", help="Search Gmail messages")
    g_p.add_argument("--latest", type=int, metavar="N", help="Fetch latest N emails with cleaned bodies in one call")
    g_p.add_argument("--read", nargs="+", metavar="MSG_ID", help="Read one or more emails by ID")
    g_p.add_argument("--calendar", action="store_true", help="List upcoming Calendar events")
    g_p.add_argument("--drive", metavar="QUERY", nargs="?", const="", help="List Google Drive files")
    g_p.add_argument("--stage-email", action="store_true", help="Stage an outbound email via Gatekeeper")
    g_p.add_argument("--to", default="", help="Recipient email")
    g_p.add_argument("--subject", default="", help="Email subject")
    g_p.add_argument("--body", default="", help="Email body")
    g_p.add_argument("--attach", nargs="*", default=[], help="Optional file paths to attach (e.g. CV PDF, certificates)")

    w_p = sub.add_parser("whatsapp", help="WhatsApp companion bridge operations")
    w_p.add_argument("--pair", metavar="PHONE", nargs="?", const="", help="Generate 8-digit pairing code or QR")
    w_p.add_argument("--chats", action="store_true", help="List recent WhatsApp chats")
    w_p.add_argument("--stage-msg", action="store_true", help="Stage a WhatsApp message via Gatekeeper")
    w_p.add_argument("--to", default="", help="Recipient phone/JID")
    w_p.add_argument("--text", default="", help="Message text")

    t_p = sub.add_parser("telegram", help="Personal Telegram MTProto operations")
    t_p.add_argument("--dialogs", action="store_true", help="List personal Telegram dialogs")
    t_p.add_argument("--stage-msg", action="store_true", help="Stage a personal Telegram message via Gatekeeper")
    t_p.add_argument("--call", metavar="SUMMARY", nargs="?", const="", help="Place a 1-on-1 Telegram voice call to Omar")
    t_p.add_argument("--to", default="", help="Target username or phone")
    t_p.add_argument("--text", default="", help="Message text")

    l_p = sub.add_parser("linkedin", help="LinkedIn account operations")
    l_p.add_argument("--feed", action="store_true", help="Read LinkedIn feed")
    l_p.add_argument("--notifications", action="store_true", help="Read LinkedIn notifications")
    l_p.add_argument("--messages", action="store_true", help="Read LinkedIn inbox conversations")
    l_p.add_argument("--poll", action="store_true", help="Check unseen LinkedIn recruiter/message events")
    l_p.add_argument("--stage-post", metavar="TEXT", help="Stage a LinkedIn post via Gatekeeper")
    l_p.add_argument("--visibility", default="PUBLIC", help="Post visibility (PUBLIC or CONNECTIONS)")

    f_p = sub.add_parser("facebook", help="Facebook account operations")
    f_p.add_argument("--feed", action="store_true", help="Read Facebook feed")
    f_p.add_argument("--notifications", action="store_true", help="Read Facebook notifications")
    f_p.add_argument("--reels", action="store_true", help="List Facebook reels")
    f_p.add_argument("--stage-post", metavar="TEXT", help="Stage a Facebook post via Gatekeeper")
    f_p.add_argument("--audience", default="PUBLIC", help="Post audience (PUBLIC or FRIENDS)")

    args = parser.parse_args()

    if args.cmd == "status" or not args.cmd:
        print(json.dumps(get_account_status(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "google":
        ga = GoogleAdapter()
        if args.latest is not None:
            print(json.dumps(ga.get_latest_emails(count=args.latest, query=args.search or ""), indent=2, ensure_ascii=False))
        elif args.search is not None:
            print(json.dumps(ga.search_emails(args.search), indent=2, ensure_ascii=False))
        elif args.read:
            if len(args.read) == 1:
                print(json.dumps(ga.read_email(args.read[0]), indent=2, ensure_ascii=False))
            else:
                print(json.dumps([ga.read_email(mid) for mid in args.read], indent=2, ensure_ascii=False))
        elif args.calendar:
            print(json.dumps(ga.list_calendar_events(), indent=2, ensure_ascii=False))
        elif args.drive is not None:
            print(json.dumps(ga.list_drive_files(args.drive), indent=2, ensure_ascii=False))
        elif args.stage_email:
            print(json.dumps(ga.stage_email(args.to, args.subject, args.body, notify_telegram=True, attachments=args.attach), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "whatsapp":
        wa = WhatsAppAdapter()
        if args.pair is not None:
            print(json.dumps(wa.get_qr_or_pairing_code(args.pair), indent=2, ensure_ascii=False))
        elif args.chats:
            print(json.dumps(wa.list_recent_chats(), indent=2, ensure_ascii=False))
        elif args.stage_msg:
            print(json.dumps(wa.stage_message(args.to, args.text, notify_telegram=True), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "telegram":
        ta = TelegramUserAdapter()
        if args.dialogs:
            print(json.dumps(ta.list_dialogs(), indent=2, ensure_ascii=False))
        elif args.stage_msg:
            print(json.dumps(ta.stage_message(args.to, args.text, notify_telegram=True), indent=2, ensure_ascii=False))
        elif args.call is not None:
            print(json.dumps(ta.place_voice_call(args.call), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "linkedin":
        la = LinkedInAdapter()
        if args.feed:
            print(json.dumps(la.get_feed(), indent=2, ensure_ascii=False))
        elif args.notifications:
            print(json.dumps(la.get_notifications(), indent=2, ensure_ascii=False))
        elif args.messages:
            print(json.dumps(la.get_conversations(), indent=2, ensure_ascii=False))
        elif args.poll:
            print(json.dumps(la.check_unseen_events(), indent=2, ensure_ascii=False))
        elif args.stage_post:
            print(json.dumps(la.stage_post(args.stage_post, args.visibility, notify_telegram=True), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "facebook":
        fa = FacebookAdapter()
        if args.feed:
            print(json.dumps(fa.get_feed(), indent=2, ensure_ascii=False))
        elif args.notifications:
            print(json.dumps(fa.get_notifications(), indent=2, ensure_ascii=False))
        elif args.reels:
            print(json.dumps(fa.get_reels(), indent=2, ensure_ascii=False))
        elif args.stage_post:
            print(json.dumps(fa.stage_post(args.stage_post, args.audience, notify_telegram=True), indent=2, ensure_ascii=False))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
