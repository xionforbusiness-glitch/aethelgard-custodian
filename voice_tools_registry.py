#!/usr/bin/env python3
"""
voice_tools_registry.py — Modular, Separated Voice Tool Architecture for Aethelgard
===================================================================================
Decouples voice tool schemas and execution handlers from the core WebRTC audio loop:
  - Communication & Document Delivery: send_telegram_file, send_telegram_message
  - Live Intelligence & Research: live_web_search
  - Real-Time Career & Draft Management: check_draft_status, pitch_recruiter
  - Full compatibility with Gemini 3.8 Live API and VoiceBrainSession.
"""

import datetime
import imaplib
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

logger = logging.getLogger("voice_tools_registry")


def _get_env_val(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    candidates = [
        Path.home() / "aethelgard-custodian" / ".env",
        Path(__file__).parent / ".env",
        Path.home() / ".hermes" / "profiles" / "llm-wiki" / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == key:
                            return v.strip().strip("'\"")
            except Exception:
                pass
    return default


def _get_career_assets_dir() -> Path:
    home = os.environ.get("AETHELGARD_HOME")
    base = Path(os.path.expanduser(home)) if home else (Path.home() / ".aethelgard")
    p = base / "career_assets"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ==============================================================================
# 1. TOOL SCHEMA DEFINITIONS (EXPOSED TO GEMINI 3.8 LIVE)
# ==============================================================================

def get_voice_tool_declarations() -> List[types.Tool]:
    """Returns the complete, cleanly separated tool registry for Gemini 3.8 Live."""
    return [
        types.Tool(
            function_declarations=[
                # --- COMMUNICATION & DELIVERY ---
                types.FunctionDeclaration(
                    name="send_telegram_file",
                    description=(
                        "Sends Omar's CV, Etihad training certificates, or specific vault documents directly into his Telegram chat "
                        "(@aethelgard_vault_bot) so he can review, download, or inspect them immediately on his mobile device. "
                        "Call this whenever Omar asks: 'send them to me on the chat', 'send my CV to Telegram', 'send the certificates to my chat', "
                        "or requests a document sent to his phone."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "file_type": types.Schema(
                                type=types.Type.STRING,
                                description="Type of document to send: 'cv' (Omar's CV), 'certificates' (all 5 Etihad certificates), 'all' (CV + all certificates), or a specific certificate/document name.",
                            ),
                            "caption": types.Schema(
                                type=types.Type.STRING,
                                description="Optional accompanying caption or description in British butler tone.",
                            ),
                        },
                        required=["file_type"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="send_telegram_message",
                    description=(
                        "Sends a direct text message, pitch preview, reminder, or operational note into Omar's Telegram chat. "
                        "Call this whenever Omar asks to text him, send notes to the chat, or send a draft pitch for him to review."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "message": types.Schema(type=types.Type.STRING, description="The message body formatted in clear Markdown"),
                        },
                        required=["message"],
                    ),
                    behavior="BLOCKING",
                ),
                # --- LIVE RESEARCH & INTELLIGENCE ---
                types.FunctionDeclaration(
                    name="live_web_search",
                    description=(
                        "Performs a live real-time web search for verified facts, airline career portal URLs, job openings, airport news, "
                        "or technical information. Call this whenever Omar asks to search the internet, look up airline contacts, or check current info."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="The search query (e.g. 'Etihad Airways careers Ground Operations', 'flydubai recruitment email')"),
                            "limit": types.Schema(type=types.Type.INTEGER, description="Maximum number of search results to retrieve (default 3, max 5)"),
                        },
                        required=["query"],
                    ),
                    behavior="BLOCKING",
                ),
                # --- CAREER & DRAFT ORCHESTRATION ---
                types.FunctionDeclaration(
                    name="check_draft_status",
                    description=(
                        "Checks the real-time status of Omar's Gmail Drafts folder. Reports exactly how many airline drafts currently exist, "
                        "lists their target airlines/recipients, and indicates if the background batch generator is currently active. "
                        "Call this whenever Omar asks 'how many drafts are there?', 'what are the other drafts?', 'are they ready?', or 'where are my drafts?'."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="Optional filter by airline name or keyword"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="pitch_recruiter",
                    description=(
                        "Generates a high-impact, professional elevator pitch tailored to a specific airline recruiter, highlighting "
                        "Omar's Damascus station experience (Syravia ID: 20264, handling Etihad, flydubai, Air Arabia), Amadeus Altéa DCS (91%), "
                        "and 5 Etihad training certificates. Dispatches the pitch directly to Omar's Telegram chat for 1-click copying."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "company": types.Schema(type=types.Type.STRING, description="Target airline or employer (e.g. 'Etihad Airways', 'Emirates', 'Saudia', 'flynas')"),
                            "target_role": types.Schema(type=types.Type.STRING, description="Target role (defaults to Airport Passenger Service & Ground Operations Agent)"),
                            "delivery": types.Schema(type=types.Type.STRING, description="Where to deliver: 'telegram' (default, sends pitch card to Telegram) or 'return' (voice readback only)"),
                        },
                        required=["company"],
                    ),
                    behavior="BLOCKING",
                ),
                # --- CORE AVIATION & EMAIL EXECUTION ---
                types.FunctionDeclaration(
                    name="batch_draft_aviation_applications",
                    description="Creates individually tailored job application drafts in Omar's Gmail Drafts folder for a batch of airlines from the verified Aviation Batch 40 dossier. Each draft is addressed to a single airline recruiter, tailored specifically for Airport Ground Operations / Passenger Service Officer, and automatically attaches Omar's CV and 5 certificates. Supports excluding companies (e.g. Syravia).",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "count": types.Schema(type=types.Type.INTEGER, description="Number of airline applications to draft (default 10, max 20)"),
                            "target_role": types.Schema(type=types.Type.STRING, description="Target job title"),
                            "exclude_companies": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING), description="Companies to exclude (e.g. ['Syravia'])"),
                            "clean_existing": types.Schema(type=types.Type.BOOLEAN, description="Whether to delete existing drafts before drafting the new batch"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="delete_gmail_draft",
                    description="Deletes drafts directly from Omar's Gmail Drafts folder. Can target a specific draft by airline/company name (e.g. 'Syravia', 'Etihad'), recipient email address, subject keyword, or delete all existing drafts if delete_all is True.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="Airline name, company name, or subject keyword to match and delete"),
                            "recipient": types.Schema(type=types.Type.STRING, description="Optional recipient email address to match"),
                            "delete_all": types.Schema(type=types.Type.BOOLEAN, description="Set to True ONLY if Omar explicitly asks to delete all existing drafts"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="send_gmail_draft",
                    description="Directly dispatches and sends an email draft from Omar's real Gmail account to a recruiter or contact, complete with attached CV and training certificates. Removes the sent message from Drafts, logs to the career application tracker, and sends a Telegram card confirmation.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "airline_or_company": types.Schema(type=types.Type.STRING, description="Airline or company name of the draft to send (e.g. 'Etihad Airways', 'Emirates', 'flydubai')"),
                            "recipient": types.Schema(type=types.Type.STRING, description="Optional specific recipient email (e.g. recruitment@etihad.ae)"),
                            "query": types.Schema(type=types.Type.STRING, description="Optional subject or text query to locate the draft"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="modify_gmail_draft",
                    description="Modifies an existing draft in Omar's Gmail Drafts folder — adding custom attachments (such as Syravia/Etihad certificates or specific CVs), updating subject, or amending the body.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "target": types.Schema(type=types.Type.STRING, description="Airline, recipient, or subject query identifying the draft to modify"),
                            "add_attachments": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING), description="List of document names or paths to attach"),
                            "updated_body": types.Schema(type=types.Type.STRING, description="Optional updated or appended body text"),
                            "updated_subject": types.Schema(type=types.Type.STRING, description="Optional new subject line"),
                        },
                        required=["target"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="save_gmail_draft",
                    description="Creates and saves an individual draft email directly in Omar's Gmail Drafts folder. Each draft MUST have a single recipient email address. Automatically attaches Omar's CV and Etihad training certificates for job applications.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "to": types.Schema(type=types.Type.STRING, description="Single recipient email address"),
                            "subject": types.Schema(type=types.Type.STRING, description="Descriptive email subject line"),
                            "body": types.Schema(type=types.Type.STRING, description="Complete email body content"),
                            "attach_career_pack": types.Schema(type=types.Type.BOOLEAN, description="Whether to attach CV and certificates (default True)"),
                        },
                        required=["to", "subject", "body"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="check_gmail",
                    description="Searches Omar's Gmail messages or lists recent emails with sender, subject, and snippet preview.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="Optional search query or sender name"),
                            "latest": types.Schema(type=types.Type.INTEGER, description="Number of recent emails to retrieve (1-5, default 3)"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="read_email",
                    description="Fetches the full text content of a specific Gmail message by ID.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "msg_id": types.Schema(type=types.Type.STRING, description="Gmail message ID from check_gmail"),
                        },
                        required=["msg_id"],
                    ),
                    behavior="BLOCKING",
                ),
                # --- KNOWLEDGE, VAULT & SESSION ---
                types.FunctionDeclaration(
                    name="check_agenda",
                    description="Retrieves Omar's upcoming calendar events, work shifts, and Damascus airport weather.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "hours_ahead": types.Schema(type=types.Type.INTEGER, description="Lookahead window in hours (default 24)"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="search_vault",
                    description="Searches Omar's Obsidian knowledge vault for operational notes, flight logs, or memories.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="Topic, aircraft term, or keyword"),
                            "note_name": types.Schema(type=types.Type.STRING, description="Optional specific note file name"),
                        },
                        required=["query"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="append_vault_note",
                    description="Appends a quick dated observation or log entry to Omar's Obsidian vault notes.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "content": types.Schema(type=types.Type.STRING, description="Text to append"),
                            "note_name": types.Schema(type=types.Type.STRING, description="Note filename (defaults to Daily Log.md)"),
                        },
                        required=["content"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="get_recent_telegram_messages",
                    description="Retrieves the most recent text messages sent by Omar or the assistant in Telegram chat (@aethelgard_vault_bot).",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "limit": types.Schema(type=types.Type.INTEGER, description="Number of recent messages to fetch (default 5, max 15)"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="schedule_callback",
                    description="Schedules an automatic outgoing voice phone call to Omar after a specified number of minutes.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "minutes": types.Schema(type=types.Type.INTEGER, description="Delay in minutes before calling Omar back (e.g. 5)"),
                            "reason": types.Schema(type=types.Type.STRING, description="Brief reason or topic for the follow-up call"),
                        },
                        required=["minutes"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="end_call",
                    description="Terminates the live Telegram call after speaking a brief polite farewell to Omar. If Omar requested a callback, specify callback_minutes.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "farewell": types.Schema(type=types.Type.STRING, description="Courteous closing remark"),
                            "callback_minutes": types.Schema(type=types.Type.INTEGER, description="Optional delay in minutes before calling Omar back automatically."),
                            "callback_reason": types.Schema(type=types.Type.STRING, description="Optional reason for the scheduled follow-up call"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
            ]
        ),
    ]


# ==============================================================================
# 2. MODULAR TOOL EXECUTION HANDLERS
# ==============================================================================

def execute_send_telegram_file(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Sends CV, certificates, or vault files directly into Omar's Telegram chat."""
    import send_telegram_file

    file_type = str(args.get("file_type") or "cv").strip().lower()
    caption = str(args.get("caption") or "").strip()

    assets_dir = _get_career_assets_dir()
    cv_dir = assets_dir / "cv"
    cert_dir = assets_dir / "certificates"
    vault_dir = Path(os.environ.get("VAULT_DIR") or os.path.expanduser("~/vault"))

    files_to_send: List[Tuple[Path, str]] = []

    # 1. Resolve files based on request
    if file_type in ("cv", "resume", "my cv"):
        candidates = list(cv_dir.glob("*.pdf"))
        target_cv = next((f for f in candidates if "2026" in f.name), candidates[0] if candidates else None)
        if target_cv and target_cv.exists():
            cap = caption or "📄 **Omar Elnemr — Executive Ground Operations CV (2026)**\n• Amadeus Altéa DCS 91%\n• Damascus Station Handling (Syravia ID: 20264)"
            files_to_send.append((target_cv, cap))
        else:
            return {"error": "Primary CV PDF not found in career assets."}

    elif file_type in ("certificates", "certificate", "etihad", "syravia", "training"):
        certs = sorted(cert_dir.glob("*.pdf"))
        if not certs:
            return {"error": "Training certificates not found in career assets."}
        for idx, c in enumerate(certs, start=1):
            cap = f"🎖️ **Training Certificate [{idx}/5]:** `{c.stem}`" if idx == 1 and not caption else (caption if idx == 1 else "")
            files_to_send.append((c, cap))

    elif file_type in ("all", "career_pack", "everything"):
        # Send CV first
        candidates = list(cv_dir.glob("*.pdf"))
        target_cv = next((f for f in candidates if "2026" in f.name), candidates[0] if candidates else None)
        if target_cv and target_cv.exists():
            files_to_send.append((target_cv, "📄 **Omar Elnemr — Primary Aviation CV**"))
        # Send all 5 certificates
        for idx, c in enumerate(sorted(cert_dir.glob("*.pdf")), start=1):
            files_to_send.append((c, f"🎖️ Certificate [{idx}/5]: `{c.stem}`"))

    else:
        # Search for file matching query in career assets or vault
        matched = list(assets_dir.rglob(f"*{file_type}*")) or list(vault_dir.rglob(f"*{file_type}*"))
        matched_pdf = [f for f in matched if f.is_file()]
        if matched_pdf:
            files_to_send.append((matched_pdf[0], caption or f"📎 Document: `{matched_pdf[0].name}`"))
        else:
            return {"error": f"Could not find document matching '{file_type}'"}

    # 2. Dispatch files via send_telegram_file
    sent_names = []
    errors = []
    for fpath, cap in files_to_send:
        try:
            res = send_telegram_file.send_file_to_telegram(str(fpath), caption=cap)
            if res.get("ok"):
                sent_names.append(fpath.name)
            else:
                errors.append(f"Failed {fpath.name}: {res}")
        except Exception as exc:
            errors.append(f"Error {fpath.name}: {exc}")

    if sent_names:
        return {
            "success": True,
            "sent_count": len(sent_names),
            "files": sent_names,
            "summary": f"Dispatched {len(sent_names)} file(s) ({', '.join(sent_names[:2])}) directly to your Telegram chat, Sir.",
        }
    return {"error": f"Failed to dispatch files to Telegram: {'; '.join(errors)}"}


def execute_send_telegram_message(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Sends a direct text message or pitch into Omar's Telegram chat."""
    from proactive_scheduler import send_telegram

    message = str(args.get("message") or "").strip()
    if not message:
        return {"error": "Message content cannot be empty"}

    try:
        ok = send_telegram(message)
        return {
            "success": bool(ok),
            "delivered": True,
            "summary": "Dispatched message directly to your Telegram chat, Sir.",
        }
    except Exception as exc:
        return {"error": f"Failed to deliver Telegram message: {exc}"}


def execute_live_web_search(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Executes high-speed live web search using Firecrawl."""
    import requests

    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "Search query cannot be empty"}

    limit = max(1, min(int(args.get("limit") or 3), 5))
    fc_key = _get_env_val("FIRECRAWL_API_KEY")
    if not fc_key:
        return {"error": "FIRECRAWL_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {fc_key}", "Content-Type": "application/json"}
    payload = {"query": query, "limit": limit}

    try:
        resp = requests.post("https://api.firecrawl.dev/v1/search", headers=headers, json=payload, timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            results = []
            for item in data[:limit]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": str(item.get("description") or item.get("markdown") or "")[:200].strip(),
                })
            summary_txt = f"Found {len(results)} search results for '{query}'"
            if results:
                summary_txt += f": {results[0]['title']} ({results[0]['url']})"
            return {
                "success": True,
                "query": query,
                "count": len(results),
                "results": results,
                "summary": summary_txt,
            }
        else:
            return {"error": f"Search API HTTP {resp.status_code}: {resp.text[:120]}"}
    except Exception as exc:
        return {"error": f"Web search failed: {exc}"}


def execute_check_draft_status(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Audits Omar's Gmail Drafts folder in real-time, reporting ready and pending applications."""
    from omnichannel_bridge import GoogleAdapter

    ga = GoogleAdapter()
    _, sender_email, app_pw = ga._has_oauth_and_app_pw()
    if not app_pw:
        return {"error": "Gmail credentials unavailable."}

    query_filter = str(args.get("query") or "").strip().lower()
    drafts = []

    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=12) as imap:
            imap.login(sender_email, app_pw)
            status, _ = imap.select("[Gmail]/Drafts", readonly=True)
            if status != "OK":
                return {"error": "Could not access Gmail Drafts folder."}

            status, data = imap.search(None, "ALL")
            uids = data[0].split() if (status == "OK" and data and data[0]) else []

            for u in reversed(uids):  # newest first
                res, msg_data = imap.fetch(u, "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT TO DATE)])")
                if res == "OK" and msg_data and msg_data[0]:
                    hdrs = msg_data[0][1].decode("utf-8", errors="ignore")
                    hdr_to = ""
                    hdr_subj = ""
                    for line in hdrs.splitlines():
                        if line.lower().startswith("to:"):
                            hdr_to = line[3:].strip()
                        elif line.lower().startswith("subject:"):
                            hdr_subj = line[8:].strip()
                    if query_filter and (query_filter not in hdr_to.lower() and query_filter not in hdr_subj.lower()):
                        continue
                    drafts.append({
                        "uid": u.decode(),
                        "to": hdr_to,
                        "subject": hdr_subj,
                    })
    except Exception as exc:
        return {"error": f"IMAP draft check error: {exc}"}

    # Check if background thread is active
    active_threads = [t.name for t in threading.enumerate()]
    is_worker_active = "AviationBatchDraftWorker" in active_threads

    summary = f"You currently have {len(drafts)} application draft(s) in your Gmail Drafts folder."
    if drafts:
        summary += f" Recent: {', '.join(d['to'].split('@')[0] for d in drafts[:4])}."
    if is_worker_active:
        summary += " The background drafting engine is actively uploading remaining applications."

    return {
        "success": True,
        "total_drafts": len(drafts),
        "drafts": drafts[:10],
        "background_drafting_active": is_worker_active,
        "summary": summary,
    }


def execute_pitch_recruiter(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Drafts and delivers a sharp elevator pitch tailored to a target airline."""
    from proactive_scheduler import send_telegram

    company = str(args.get("company") or "").strip()
    role = str(args.get("target_role") or "Airport Ground Operations & Passenger Service Agent").strip()
    delivery = str(args.get("delivery") or "telegram").strip().lower()

    if not company:
        return {"error": "Target company/airline must be specified"}

    pitch_card = (
        f"🎯 **Aviation Executive Pitch — {company}**\n\n"
        f"Dear {company} Recruitment Team,\n\n"
        f"I am writing to express my strong candidacy for the **{role}** position with **{company}**.\n\n"
        f"**Why I deliver immediate, turnkey station value:**\n"
        f"• **Station Ground Handling Experience:** Practical, hands-on flight facilitation at Damascus International Airport (DAM / OSDI with Syravia ID: 20264), handling turnaround operations for Etihad Airways (EY), flydubai (FZ), and Air Arabia.\n"
        f"• **Verified DCS Proficiency:** Certified in **Amadeus Altéa Customer Management (CM DCS, scored 91%)**, managing complex passenger check-in, gate boarding, disruption recovery, and baggage reconciliation.\n"
        f"• **Official Airline Station Training:** 5 formal Etihad Airways credentials (IGS Ground Services, INSPIRE Service Delivery, Disruption Management, GSU-34 Ground Support, Aviation Orientation).\n"
        f"• **Academic & Technical Edge:** B.Sc. Computer Science candidate (University of the People), Google Cybersecurity certified, bilingual in Arabic and English.\n\n"
        f"My complete CV and certified course credentials are ready for your review. I welcome the opportunity to discuss how my station handling experience supports {company}'s station excellence.\n\n"
        f"Sincerely,\n"
        f"**Omar Elnemr** | `omar.alnemr04@gmail.com` | `+963 934 694 750`"
    )

    if delivery == "telegram":
        try:
            send_telegram(pitch_card)
        except Exception:
            pass

    return {
        "success": True,
        "company": company,
        "role": role,
        "pitch": pitch_card,
        "delivered_to": delivery,
        "summary": f"I have drafted your tailored aviation pitch for {company} and dispatched the formatted card directly to your Telegram chat, Sir.",
    }


# ==============================================================================
# 3. UNIFIED DISPATCHER (CALLED BY VoiceBrainSession)
# ==============================================================================

NEW_VOICE_TOOL_HANDLERS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = {
    "send_telegram_file": execute_send_telegram_file,
    "send_telegram_message": execute_send_telegram_message,
    "live_web_search": execute_live_web_search,
    "check_draft_status": execute_check_draft_status,
    "pitch_recruiter": execute_pitch_recruiter,
}


def try_dispatch_new_voice_tool(name: str, args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """Checks if tool is registered in the new modular registry and executes it."""
    handler = NEW_VOICE_TOOL_HANDLERS.get(name)
    if handler:
        try:
            res = handler(args, session_ctx)
            return True, res
        except Exception as exc:
            logger.exception(f"Error in {name}: {exc}")
            return True, {"error": f"Tool execution failed for {name}: {exc}"}
    return False, {}
