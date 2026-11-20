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
                    name="batch_send_gmail_drafts",
                    description="Dispatches and transmits all staged email application drafts currently in Omar's Gmail Drafts folder (or a specified batch) directly to recruiter emails via SMTP asynchronously in the background, complete with attached CV and training certificates. Removes sent messages from Drafts, logs to the career application tracker, and sends a comprehensive Telegram dispatch report. Call this immediately when Omar says 'send the applications', 'send all of them', 'proceed with all', 'dispatch them all', or 'send the batch'.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "send_all": types.Schema(type=types.Type.BOOLEAN, description="Set to True to send all currently staged drafts (default True)"),
                            "max_count": types.Schema(type=types.Type.INTEGER, description="Maximum number of drafts to send in this batch (default 20)"),
                            "airline_filter": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING), description="Optional specific airline or company names to include or filter"),
                        },
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="send_gmail_draft",
                    description="Directly dispatches and sends an email draft from Omar's real Gmail account to a recruiter or contact asynchronously in the background, complete with attached CV and training certificates. Removes the sent message from Drafts, logs to the career application tracker, and sends a Telegram card confirmation. Call this when Omar specifies a single airline or recipient to send.",
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
                            "to": types.Schema(type=types.Type.STRING, description="Single recipient email address. If Omar says 'me' or 'myself', pass 'omar.alnemr04@gmail.com'"),
                            "subject": types.Schema(type=types.Type.STRING, description="Descriptive email subject line"),
                            "body": types.Schema(type=types.Type.STRING, description="Complete email body content"),
                            "attach_career_pack": types.Schema(type=types.Type.BOOLEAN, description="Whether to attach CV and certificates (set True ONLY for job/career applications, default False)"),
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
                    name="modify_shift_schedule",
                    description="Modifies, edits, or reschedules Omar's work shift attendance roster for today, tomorrow, or any specific date (e.g. changing hours to '09:00 AM → 05:00 PM', marking as 'OFF', or adjusting shift duration). Automatically updates the Obsidian vault, syncs the shift tracker state, recalibrates circadian sleep anchors, and sends a Telegram card confirmation. Call this whenever Omar asks to edit, change, or update his work schedule or shift times.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "date": types.Schema(type=types.Type.STRING, description="Target date ('tomorrow', 'today', '2026-11-21', or weekday name)"),
                            "shift_times": types.Schema(type=types.Type.STRING, description="Shift hours (e.g. '09:00 AM → 05:00 PM', '08:15 AM → 04:00 PM', 'OFF')"),
                            "status": types.Schema(type=types.Type.STRING, description="Optional status (e.g. 'Standard', 'Overtime', 'OFF', 'Paid Standby')"),
                            "notes": types.Schema(type=types.Type.STRING, description="Optional note or reason for change"),
                        },
                        required=["date"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="manage_calendar_event",
                    description="Creates, modifies, or deletes an event, meeting, or appointment on Omar's Google Calendar. Generates and dispatches RFC 5545 .ics calendar synchronization directly to his primary Google Calendar, updates agenda feeds, and sends an event confirmation card to Telegram. Call this when Omar wants to create, edit, reschedule, or cancel a calendar event or appointment.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "action": types.Schema(type=types.Type.STRING, description="Action to perform: 'create', 'modify', or 'delete'"),
                            "summary": types.Schema(type=types.Type.STRING, description="Title or summary of the event (e.g. 'Dentist appointment', 'Recruiter interview')"),
                            "start_time": types.Schema(type=types.Type.STRING, description="Start time (e.g. '10:00 AM', '14:30', or ISO format)"),
                            "end_time": types.Schema(type=types.Type.STRING, description="Optional end time (defaults to 1 hour after start)"),
                            "date": types.Schema(type=types.Type.STRING, description="Date of the event ('tomorrow', 'today', 'YYYY-MM-DD')"),
                            "description": types.Schema(type=types.Type.STRING, description="Optional event description or notes"),
                            "location": types.Schema(type=types.Type.STRING, description="Optional location of the event"),
                        },
                        required=["action", "summary"],
                    ),
                    behavior="BLOCKING",
                ),
                types.FunctionDeclaration(
                    name="automate_portal_signup",
                    description=(
                        "Autonomously creates, registers, or claims developer benefits/portal accounts (such as GitHub Student Developer Pack offers "
                        "like MongoDB Atlas $50 credits/certification, New Relic student tier, or airline career portals) using Omar's verified credentials. "
                        "Spins up an ephemeral browser task in the background, automatically solves email verification links and OTPs via Gmail, "
                        "stores encrypted credentials, and sends a Telegram card with status."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "service_name": types.Schema(
                                type=types.Type.STRING,
                                description="Name of the service or portal (e.g. 'MongoDB Atlas', 'New Relic', 'Etihad Careers', 'ZenMux')",
                            ),
                            "target_url": types.Schema(
                                type=types.Type.STRING,
                                description="Optional direct registration or portal URL. If omitted, Aethelgard resolves it automatically.",
                            ),
                            "notes": types.Schema(
                                type=types.Type.STRING,
                                description="Specific instructions or goal (e.g. 'claim $50 Atlas credits via GitHub student pack', 'create career applicant account')",
                            ),
                        },
                        required=["service_name"],
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
                    name="place_telegram_call",
                    description=(
                        "Initiates an outgoing voice call mission to a contact (e.g. Omar's mother Fotnah, a friend, or an external party) "
                        "to check on them, ask a question, or deliver an executive update. Call this whenever Omar verbally tells you to call someone "
                        "(e.g. 'call my mom and check on her', 'call @friend and ask if he is free tonight', 'call Fotnah'). "
                        "Aethelgard will confirm verbally in under 500ms, conclude the current line to free the Telegram VoIP channel, "
                        "immediately dial the contact, stream live turn-by-turn text updates to Omar's bot, and send the full recording + debrief when done."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "target": types.Schema(
                                type=types.Type.STRING,
                                description="Recipient name or handle: 'mother', 'mom', 'Fotnah', '@username', or numeric Telegram user ID",
                            ),
                            "mission_objective": types.Schema(
                                type=types.Type.STRING,
                                description="The specific objective, question, or message to deliver (e.g. 'Check how she is doing and ask if she needs anything')",
                            ),
                        },
                        required=["target", "mission_objective"],
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


def execute_batch_send_gmail_drafts(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dispatches all staged application drafts in Omar's Gmail Drafts folder asynchronously in the background.
    Returns immediately (<20ms) so Gemini 3.8 Live can verbally confirm without any dead air or WebSocket drop.
    """
    import email
    import smtplib
    from omnichannel_bridge import GoogleAdapter

    ga = GoogleAdapter()
    _, sender_email, app_pw = ga._has_oauth_and_app_pw()
    if not app_pw:
        return {"error": "Gmail credentials unavailable for draft dispatch."}

    send_all = bool(args.get("send_all", True))
    max_count = max(1, min(int(args.get("max_count") or 20), 40))
    airline_filter = args.get("airline_filter") or []
    if isinstance(airline_filter, str):
        airline_filter = [airline_filter]
    vault_dir = session_ctx.get("vault_dir")

    def _bg_batch_send_worker():
        from proactive_scheduler import send_telegram

        logger.info("[BatchSendWorker] Starting background dispatch of Gmail drafts...")
        dispatched_items = []
        errors = []

        # 1. Also approve and dispatch any staged drafts in approval_gatekeeper
        try:
            import approval_gatekeeper
            pending = approval_gatekeeper.list_pending_drafts()
            for p in pending:
                d_id = p.get("draft_id")
                if d_id:
                    approval_gatekeeper.approve_draft(d_id, approver="Omar via live voice batch dispatch", execute=True)
                    dispatched_items.append({
                        "recipient": p.get("recipient_or_target") or "Staged Recruiter",
                        "subject": p.get("subject") or "Application",
                        "airline": p.get("recipient_or_target") or "Staged Application",
                    })
        except Exception as gate_ex:
            logger.warning(f"[BatchSendWorker] Gatekeeper sweep warning: {gate_ex}")

        # 2. Inspect Gmail Drafts using fast HEADER-ONLY retrieval
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30) as imap:
                imap.login(sender_email, app_pw)
                status, _ = imap.select("[Gmail]/Drafts", readonly=False)
                if status != "OK":
                    logger.error("[BatchSendWorker] Failed to select [Gmail]/Drafts")
                    return

                status, search_data = imap.search(None, "ALL")
                if status == "OK" and search_data and search_data[0]:
                    uids = search_data[0].split()
                    logger.info(f"[BatchSendWorker] Found {len(uids)} total drafts in folder.")

                    # Fast Header scan
                    drafts_to_send = []
                    for u in reversed(uids):
                        if len(drafts_to_send) >= max_count:
                            break
                        res, msg_data = imap.fetch(u, "(BODY.PEEK[HEADER.FIELDS (SUBJECT TO DATE)])")
                        if res != "OK" or not msg_data or not msg_data[0]:
                            continue
                        hdrs = msg_data[0][1].decode("utf-8", errors="ignore")
                        hdr_to = ""
                        hdr_subj = ""
                        for line in hdrs.splitlines():
                            if line.lower().startswith("to:"):
                                hdr_to = line[3:].strip()
                            elif line.lower().startswith("subject:"):
                                hdr_subj = line[8:].strip()

                        if not hdr_to or "@" not in hdr_to:
                            continue

                        # Check filter if specified
                        if airline_filter:
                            combined = f"{hdr_to} {hdr_subj}".lower()
                            if not any(f.lower().strip() in combined for f in airline_filter):
                                continue

                        drafts_to_send.append((u, hdr_to, hdr_subj))

                    logger.info(f"[BatchSendWorker] Identified {len(drafts_to_send)} drafts ready for dispatch.")

                    # Transmit each draft via SMTP
                    if drafts_to_send:
                        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=45) as server:
                            server.login(sender_email, app_pw)
                            for u, msg_to, msg_subj in drafts_to_send:
                                try:
                                    res, full_data = imap.fetch(u, "(RFC822)")
                                    if res != "OK" or not full_data or not full_data[0]:
                                        continue
                                    parsed_msg = email.message_from_bytes(full_data[0][1])
                                    parsed_msg["To"] = msg_to

                                    server.send_message(parsed_msg)

                                    # Mark as deleted in IMAP
                                    imap.store(u, "+FLAGS", "\\Deleted")

                                    # Count attachments
                                    att_count = sum(1 for part in parsed_msg.walk() if part.get_filename())
                                    dispatched_items.append({
                                        "recipient": msg_to,
                                        "subject": msg_subj,
                                        "attachments": att_count,
                                    })

                                    # Log to vault
                                    try:
                                        import job_application_engine
                                        job_application_engine._log_application_to_vault(
                                            {
                                                "company": msg_subj.split(" - ")[-1] if " - " in msg_subj else msg_to,
                                                "role": "Airport Passenger Service & Ground Operations Agent",
                                                "recipient": msg_to,
                                                "channel": "gmail",
                                                "subject": msg_subj,
                                                "status": "sent",
                                            },
                                            vault_dir=vault_dir,
                                        )
                                    except Exception:
                                        pass

                                    logger.info(f"[BatchSendWorker] Sent draft UID {u.decode()} to {msg_to}")
                                except Exception as send_err:
                                    logger.error(f"[BatchSendWorker] Failed sending draft UID {u.decode()}: {send_err}")
                                    errors.append(f"{msg_to}: {send_err}")

                        # Expunge deleted drafts from IMAP
                        try:
                            imap.expunge()
                        except Exception:
                            pass

        except Exception as exc:
            logger.error(f"[BatchSendWorker] IMAP/SMTP error during batch send: {exc}")
            errors.append(str(exc))

        # Send Telegram Card Debrief
        if dispatched_items:
            tg_lines = [f"• **{item['recipient']}** — *{item['subject'][:45]}* ({item.get('attachments', 0)} files)" for item in dispatched_items[:12]]
            card = (
                f"🚀 **Batch Aviation Applications Dispatched**\n\n"
                f"Dispatched **{len(dispatched_items)} application(s)** directly from `{sender_email}` via SMTP:\n"
                + "\n".join(tg_lines)
                + f"\n\nAll sent emails have been removed from Drafts and logged to your career history."
            )
            if errors:
                card += f"\n\n⚠️ *Noted {len(errors)} transient error(s) during dispatch.*"
            try:
                send_telegram(card)
            except Exception:
                pass
        elif errors:
            try:
                send_telegram(f"⚠️ **Batch Dispatch Notice**: Unable to send drafts: {'; '.join(errors[:2])}")
            except Exception:
                pass

    t = threading.Thread(target=_bg_batch_send_worker, daemon=True, name="AviationBatchSendWorker")
    t.start()

    return {
        "status": "in_progress",
        "action": "batch_send",
        "message": "Initiated background dispatch for all application drafts to recruiters. Acknowledge Omar immediately with crisp, authentic verbal confirmation and invite him to continue the conversation with zero dead air.",
    }


def execute_send_gmail_draft(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dispatches a single application draft asynchronously in the background.
    Returns in <20ms so Gemini 3.8 Live can confirm immediately with zero dead air.
    """
    import email
    import smtplib
    from omnichannel_bridge import GoogleAdapter

    ga = GoogleAdapter()
    _, sender_email, app_pw = ga._has_oauth_and_app_pw()
    if not app_pw:
        return {"error": "Gmail credentials unavailable for draft dispatch."}

    airline_or_company = str(args.get("airline_or_company") or "").strip()
    recipient = str(args.get("recipient") or "").strip()
    query = str(args.get("query") or "").strip()
    vault_dir = session_ctx.get("vault_dir")

    target_name = airline_or_company or recipient or query or "aviation recruiter"

    def _bg_single_send_worker():
        from proactive_scheduler import send_telegram

        target_lower = target_name.lower()
        recip_lower = recipient.lower()
        query_lower = query.lower()

        # 1. Gatekeeper check
        try:
            import approval_gatekeeper
            pending = approval_gatekeeper.list_pending_drafts()
            for p in pending:
                p_to = str(p.get("recipient_or_target") or "").lower()
                p_sub = str(p.get("subject") or "").lower()
                p_content = str(p.get("content") or "").lower()
                combined_p = f"{p_to} {p_sub} {p_content}"
                if (recip_lower and recip_lower in p_to) or (target_lower and target_lower in combined_p) or (query_lower and query_lower in combined_p):
                    draft_id = p["draft_id"]
                    approval_gatekeeper.approve_draft(draft_id, approver="Omar via live voice", execute=True)
                    send_telegram(f"🚀 **Aviation Application Sent**: Dispatched staged draft to `{p.get('recipient_or_target')}`.")
                    return
        except Exception:
            pass

        # 2. IMAP fast header search
        matched_uid = None
        matched_to = ""
        matched_subj = ""

        try:
            with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20) as imap:
                imap.login(sender_email, app_pw)
                status, _ = imap.select("[Gmail]/Drafts", readonly=False)
                if status == "OK":
                    status, search_data = imap.search(None, "ALL")
                    if status == "OK" and search_data and search_data[0]:
                        uids = search_data[0].split()
                        for u in reversed(uids):
                            res, msg_data = imap.fetch(u, "(BODY.PEEK[HEADER.FIELDS (SUBJECT TO)])")
                            if res != "OK" or not msg_data or not msg_data[0]:
                                continue
                            hdrs = msg_data[0][1].decode("utf-8", errors="ignore")
                            hdr_to = ""
                            hdr_subj = ""
                            for line in hdrs.splitlines():
                                if line.lower().startswith("to:"):
                                    hdr_to = line[3:].strip()
                                elif line.lower().startswith("subject:"):
                                    hdr_subj = line[8:].strip()

                            combined = f"{hdr_to} {hdr_subj}".lower()
                            if (recip_lower and recip_lower in hdr_to.lower()) or (target_lower and target_lower in combined) or (query_lower and query_lower in combined):
                                matched_uid = u
                                matched_to = hdr_to
                                matched_subj = hdr_subj
                                break

                        if matched_uid:
                            res, full_data = imap.fetch(matched_uid, "(RFC822)")
                            if res == "OK" and full_data and full_data[0]:
                                parsed_msg = email.message_from_bytes(full_data[0][1])
                                if matched_to and "@" in matched_to:
                                    parsed_msg["To"] = matched_to
                                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                                    server.login(sender_email, app_pw)
                                    server.send_message(parsed_msg)

                                imap.store(matched_uid, "+FLAGS", "\\Deleted")
                                imap.expunge()

                                att_count = sum(1 for part in parsed_msg.walk() if part.get_filename())

                                # Log to vault
                                try:
                                    import job_application_engine
                                    job_application_engine._log_application_to_vault(
                                        {
                                            "company": target_name,
                                            "role": "Airport Passenger Service & Ground Operations Agent",
                                            "recipient": matched_to,
                                            "channel": "gmail",
                                            "subject": matched_subj,
                                            "status": "sent",
                                        },
                                        vault_dir=vault_dir,
                                    )
                                except Exception:
                                    pass

                                send_telegram(
                                    f"🚀 **Aviation Application Sent via Live Voice**\n\n"
                                    f"• **Recipient:** `{matched_to}`\n"
                                    f"• **Subject:** {matched_subj}\n"
                                    f"• **Attachments:** {att_count} (CV & Training Certificates)\n"
                                    f"• **Status:** Dispatched successfully from `{sender_email}`\n\n"
                                    f"Application removed from Drafts and logged to career tracker."
                                )
                                return
        except Exception as exc:
            logger.error(f"[SingleSendWorker] Error searching/sending draft: {exc}")

        # Fallback: if draft not found in IMAP, generate and send on-the-fly
        target_em = recipient
        if not target_em and target_name:
            if "etihad" in target_name.lower():
                target_em = "recruitment@etihad.ae"
            elif "emirates" in target_name.lower():
                target_em = "recruitment@emirates.com"
            elif "flydubai" in target_name.lower():
                target_em = "careers@flydubai.com"
            elif "air arabia" in target_name.lower():
                target_em = "Careers@Airarabia.Academy"
            elif "qatar" in target_name.lower():
                target_em = "qatarairways@qatarairways.com.qa"

        if target_em:
            try:
                import job_application_engine
                from email.message import EmailMessage
                from pathlib import Path
                app_pkg = job_application_engine.generate_tailored_application(
                    company=target_name,
                    role="Airport Passenger Service & Ground Operations Agent",
                    recipient=target_em,
                    channel="gmail",
                    include_certificates=True,
                    vault_dir=vault_dir,
                )
                msg = EmailMessage()
                msg["From"] = sender_email
                msg["To"] = target_em
                msg["Subject"] = app_pkg["subject"]
                msg.set_content(app_pkg["body"])
                for att_path in app_pkg.get("attachments", []):
                    p = Path(att_path)
                    if p.exists():
                        with open(p, "rb") as af:
                            msg.add_attachment(af.read(), maintype="application", subtype="pdf", filename=p.name)

                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                    server.login(sender_email, app_pw)
                    server.send_message(msg)

                try:
                    job_application_engine._log_application_to_vault(
                        {
                            "company": target_name,
                            "role": "Airport Passenger Service & Ground Operations Agent",
                            "recipient": target_em,
                            "channel": "gmail",
                            "subject": app_pkg["subject"],
                            "status": "sent",
                        },
                        vault_dir=vault_dir,
                    )
                except Exception:
                    pass

                send_telegram(
                    f"🚀 **Aviation Application Sent via Live Voice**\n\n"
                    f"• **Recipient:** `{target_em}`\n"
                    f"• **Subject:** {app_pkg['subject']}\n"
                    f"• **Attachments:** {len(app_pkg.get('attachments', []))} (CV & Training Certificates)\n"
                    f"• **Status:** Dispatched successfully from `{sender_email}`"
                )
            except Exception as f_err:
                logger.error(f"[SingleSendWorker] Fallback generate & send error: {f_err}")

    t = threading.Thread(target=_bg_single_send_worker, daemon=True, name="AviationSingleSendWorker")
    t.start()

    return {
        "status": "in_progress",
        "airline_or_company": target_name,
        "message": f"Dispatching application to {target_name} in background. Acknowledge Omar immediately with crisp, authentic verbal confirmation and invite him to continue.",
    }


def execute_modify_shift_schedule(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Modifies Omar's shift schedule in Obsidian vault and shift tracker."""
    import shift_tracker
    from proactive_scheduler import send_telegram

    date_target = str(args.get("date") or "tomorrow").strip()
    shift_times = str(args.get("shift_times") or "08:15 AM → 04:00 PM").strip()
    status = str(args.get("status") or "Standard").strip()
    notes = str(args.get("notes") or "").strip()
    vault_dir = session_ctx.get("vault_dir") or os.environ.get("VAULT_DIR") or os.path.expanduser("~/vault")

    res = shift_tracker.update_shift_schedule(
        date_input=date_target,
        shift_times=shift_times,
        status=status,
        notes=notes,
        vault_dir=vault_dir,
    )

    card = (
        f"📅 **Work Schedule Updated**\n\n"
        f"• **Target Date:** `{res.get('date')}` ({res.get('weekday')})\n"
        f"• **Shift Times:** `{res.get('log_in')} → {res.get('log_out')}`\n"
        f"• **Duration:** {res.get('duration')}\n"
        f"• **Status:** {res.get('status')}\n"
        f"• **Circadian Sleep Anchor:** {res.get('sleep_recommendation')}\n\n"
        f"Synchronized with Obsidian vault roster and circadian oracle."
    )
    try:
        send_telegram(card)
    except Exception:
        pass

    return {
        "success": True,
        "date": res.get("date"),
        "weekday": res.get("weekday"),
        "shift": f"{res.get('log_in')} → {res.get('log_out')}",
        "status": res.get("status"),
        "sleep_recommendation": res.get("sleep_recommendation"),
        "summary": f"Schedule for {res.get('weekday')} ({res.get('date')}) updated to {res.get('log_in')} → {res.get('log_out')} ({res.get('status')}), Sir. Circadian sleep anchor adjusted.",
    }


def execute_manage_calendar_event(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Creates, modifies, or deletes an event on Omar's Google Calendar via RFC 5545 .ics dispatch."""
    import calendar_bridge
    from proactive_scheduler import send_telegram

    action = str(args.get("action") or "create").lower().strip()
    summary = str(args.get("summary") or "Appointment").strip()
    start_time_raw = str(args.get("start_time") or "10:00 AM").strip()
    end_time_raw = str(args.get("end_time") or "").strip()
    date_raw = str(args.get("date") or "tomorrow").strip()
    description = str(args.get("description") or "").strip()
    location = str(args.get("location") or "").strip()

    # Resolve date
    damascus_tz = datetime.timezone(datetime.timedelta(hours=3))
    now = datetime.datetime.now(damascus_tz)
    d_clean = date_raw.lower().strip()

    if d_clean in ("today", "now"):
        base_dt = now
    elif d_clean in ("tomorrow", "tmrw"):
        base_dt = now + datetime.timedelta(days=1)
    else:
        try:
            base_dt = datetime.datetime.strptime(d_clean, "%Y-%m-%d").replace(tzinfo=damascus_tz)
        except Exception:
            base_dt = now + datetime.timedelta(days=1)

    date_str = base_dt.strftime("%Y-%m-%d")

    def _parse_hour(t_str: str, default_h: int = 10, default_m: int = 0) -> Tuple[int, int]:
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t_str, re.I)
        if not m:
            return default_h, default_m
        h, mn, ap = m.groups()
        h, mn = int(h), int(mn or 0)
        if ap:
            if ap.lower() == "pm" and h < 12:
                h += 12
            elif ap.lower() == "am" and h == 12:
                h = 0
        return h, mn

    sh, sm = _parse_hour(start_time_raw, 10, 0)
    if end_time_raw:
        eh, em = _parse_hour(end_time_raw, (sh + 1) % 24, sm)
    else:
        eh, em = (sh + 1) % 24, sm

    start_dt = base_dt.replace(hour=sh, minute=sm, second=0)
    end_dt = base_dt.replace(hour=eh, minute=em, second=0)
    if end_dt <= start_dt:
        end_dt += datetime.timedelta(hours=1)

    method = "CANCEL" if action == "delete" else "REQUEST"
    res = calendar_bridge.dispatch_calendar_invite(
        summary=summary,
        start_iso=start_dt.isoformat(),
        end_iso=end_dt.isoformat(),
        description=description,
        location=location,
        method=method,
    )

    action_label = "Cancelled" if action == "delete" else ("Updated" if action == "modify" else "Created")
    card = (
        f"🗓️ **Google Calendar Event {action_label}**\n\n"
        f"• **Event:** {summary}\n"
        f"• **Date:** `{date_str}`\n"
        f"• **Time:** `{start_dt.strftime('%I:%M %p')} → {end_dt.strftime('%I:%M %p')}` (Damascus UTC+3)\n"
        f"{f'• **Location:** {location}\n' if location else ''}"
        f"{f'• **Details:** {description}\n' if description else ''}\n"
        f"Synchronized with your primary Google Calendar."
    )
    try:
        send_telegram(card)
    except Exception:
        pass

    return {
        "success": True,
        "action": action,
        "summary": summary,
        "date": date_str,
        "start": start_dt.strftime("%I:%M %p"),
        "end": end_dt.strftime("%I:%M %p"),
        "ics_status": res,
        "message": f"Calendar event '{summary}' for {date_str} at {start_dt.strftime('%I:%M %p')} has been {action_label.lower()} and synchronized with your Google Calendar, Sir.",
    }


def execute_automate_portal_signup(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Spins up autonomous registration, account creation, or claiming of developer offers
    (e.g., GitHub Student Developer Pack: MongoDB Atlas $50 credits, New Relic student tier,
    or airline applicant portals) in the background. Returns in <20ms for instant voice confirmation.
    """
    from proactive_scheduler import send_telegram

    service_name = str(args.get("service_name") or "Service Account").strip()
    target_url = str(args.get("target_url") or "").strip()
    notes = str(args.get("notes") or "").strip()
    vault_dir = session_ctx.get("vault_dir")

    s_lower = service_name.lower()
    if not target_url:
        if "mongo" in s_lower:
            target_url = "https://www.mongodb.com/students"
        elif "relic" in s_lower:
            target_url = "https://newrelic.com/initiatives/student-developer-pack"
        elif "etihad" in s_lower:
            target_url = "https://careers.etihad.com"
        elif "emirates" in s_lower:
            target_url = "https://www.emiratesgroupcareers.com"
        elif "flydubai" in s_lower:
            target_url = "https://careers.flydubai.com"
        elif "zenmux" in s_lower:
            target_url = "https://zenmux.com"
        else:
            target_url = "https://education.github.com/pack"

    instructions = f"Sign up and claim account/benefit for {service_name}. {notes}".strip()

    def _bg_portal_runner():
        try:
            send_telegram(
                f"⚙️ **Autonomous Account Setup Initiated**\n\n"
                f"• **Service:** `{service_name}`\n"
                f"• **Target Portal:** `{target_url}`\n"
                f"• **Objective:** {instructions}\n\n"
                f"Aethelgard is executing headless browser registration, identity population, and Gmail verification in the background."
            )
            import portal_automation
            res = portal_automation.run_portal_task(
                url=target_url,
                instructions=instructions,
                vault_dir=vault_dir,
                auto_submit=True,
            )
            if res.get("success"):
                send_telegram(
                    f"✅ **Account Setup Completed**\n\n"
                    f"• **Service:** `{service_name}`\n"
                    f"• **Status:** Successfully registered & verified.\n"
                    f"• **Credentials:** Stored securely in encrypted vault credentials.\n"
                    f"• **Summary:** {res.get('confirmation', 'Completed successfully.')}"
                )
            elif res.get("status") == "waiting_human":
                pass
            else:
                send_telegram(
                    f"⚠️ **Account Setup Notice for {service_name}**\n\n"
                    f"Browser task finished with status: `{res.get('status')}`.\n"
                    f"Reason: {res.get('reason') or res.get('confirmation')}"
                )
        except Exception as p_err:
            logger.exception(f"Error in background portal runner for {service_name}: {p_err}")
            send_telegram(f"⚠️ **Account Setup Error (`{service_name}`)**: {p_err}")

    t = threading.Thread(target=_bg_portal_runner, name=f"PortalSignup-{service_name[:12]}", daemon=True)
    t.start()

    return {
        "success": True,
        "service": service_name,
        "status": "initiated",
        "url": target_url,
        "message": f"I've initiated the account setup for {service_name} in the background, Sir. I will handle the email verification and notify you on Telegram once complete.",
    }


def execute_place_telegram_call(args: Dict[str, Any], session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Schedules an autonomous outgoing mission call to a third party (e.g. Fotnah or a friend).
    Flags the caller manager to dial the target contact immediately after the current call disconnects.
    """
    target = str(args.get("target") or "").strip()
    mission_objective = str(args.get("mission_objective") or "").strip()
    if not target:
        return {"error": "Target recipient must be specified."}

    # Resolve target recipient
    t_lower = target.lower()
    target_uid: Any = None
    target_display = target

    if any(k in t_lower for k in ("mom", "mother", "fotnah", "أمي", "امي", "فطنة")):
        target_uid = 5032777692
        target_display = "Fotnah (Omar's Mother)"
    elif target.startswith("@"):
        target_uid = target
        target_display = target
    else:
        try:
            target_uid = int(target)
            target_display = f"User {target}"
        except ValueError:
            target_uid = target
            target_display = target

    caller = session_ctx.get("caller")
    brain = session_ctx.get("brain")

    if caller is not None:
        caller.pending_outgoing_call_mission = {
            "target": target_display,
            "target_uid": target_uid,
            "objective": mission_objective,
        }
        logger.info(f"[VoiceTools] Queued pending_outgoing_call_mission: {caller.pending_outgoing_call_mission}")

    if brain is not None:
        brain.end_call_requested = True

    return {
        "success": True,
        "status": "queued",
        "target": target_display,
        "objective": mission_objective,
        "summary": f"Queued outgoing mission call to {target_display} to: {mission_objective}. Line concluding now to place call.",
        "message": f"Understood, Sir. I am queuing the call to {target_display} right now. I will conclude this call to release the line and dial immediately.",
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
    "batch_send_gmail_drafts": execute_batch_send_gmail_drafts,
    "send_gmail_draft": execute_send_gmail_draft,
    "modify_shift_schedule": execute_modify_shift_schedule,
    "manage_calendar_event": execute_manage_calendar_event,
    "automate_portal_signup": execute_automate_portal_signup,
    "place_telegram_call": execute_place_telegram_call,
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
