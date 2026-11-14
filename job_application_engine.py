#!/usr/bin/env python3
"""
Aethelgard Autonomous Job Application & Career Engine (job_application_engine.py)
---------------------------------------------------------------------------------
Zero-refusal career assistant engine that locates Omar Elnemr's verified CVs,
Etihad Airways L&D certificates, and Obsidian Vault work history; optionally
ingests job posting URLs via reel_and_url_ingest; drafts tailored cover letters
and recruiter pitches; attaches primary CV + relevant certificates; stages every
outbound application through the Strict Two-Phase Approval Gatekeeper; and
logs every application to $VAULT_DIR/career/job_applications_tracker.md + log.md.
"""

from __future__ import annotations

import os
import re
import sys
import json
import argparse
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import approval_gatekeeper
import reel_and_url_ingest

# Authoritative baseline identity verified from Omar Elnemr's CVs and Vault
OMAR_IDENTITY: Dict[str, Any] = {
    "name": "Omar Elnemr",
    "location": "Damascus, Syria (DAM / OSDI)",
    "phone": "+963 0934694750",
    "email": "omar.alnemr04@gmail.com",
    "linkedin": "https://www.linkedin.com/in/omar-el-nemr",
    "languages": ["Arabic (Native)", "English (Fluent / C1-C2 Professional)"],
    "education": [
        "B.Sc. Computer Science — University of the People (UoPeople, In Progress)",
        "Software Engineering / IT — Syrian Virtual University (SVU)",
        "Google Cybersecurity Professional Certificate",
    ],
    "core_highlights": {
        "aviation": [
            "Airport Passenger & Ground Operations Agent at SERAVIA (Damascus International Airport DAM/OSDI, Aug 2026 – Nov 2026) handling Etihad Airways (EY), Turkish Airlines (TK), flydubai (FZ), Air Arabia (G9), and Syrian Airlines (RB / active individual DCS operator account).",
            "Awarded 5 official Etihad Airways L&D certificates including Amadeus Altéa Customer Management (CM) 32 Hrs with 91% score, Introduction to Ground Services (16 Hrs), INSPIRE Airport Service Delivery (16 Hrs), Airport Disruption Management Workshop, and AUG Edition 2026-GSU-34.",
            "Experienced across check-in, boarding gates, baggage/ramp coordination, irregular operations (IROPS), travel agency GDS ticketing (Hope Road, El Synable, Nour Aldeen), and regulatory compliance.",
        ],
        "tech": [
            "AI Data Evaluator & Prompt Specialist at Alignerr, Outlier, RWS, and DataForce (Jan 2025 – Present): RLHF grading, Python/SQL/algorithm validation, technical prompt engineering, and rubric evaluation.",
            "CS & Software Engineering academic track (UoPeople B.Sc. Computer Science & SVU) plus Google Cybersecurity Professional Certificate (IAM, incident response, Linux/Python security automation).",
            "Operations & multilingual client support background (Movo Delivery dispatch/QA and Intelcia / Optimum bilingual customer operations).",
        ],
    },
}

CV_PRIORITY_ORDER = [
    "Omar_Elnemr_CV_2026.pdf",
    "Omar.pdf",
    "Omar_Version1_With_Bullets.pdf",
    "Omar_Version2_Without_Bullets.pdf",
    "Omar_Elnemr_CV_2026.docx",
    "Omar.docx",
]

AVIATION_KEYWORDS = (
    "airline", "airport", "aviation", "ground", "passenger", "amadeus", "altea",
    "altéa", "dcs", "check-in", "checkin", "ramp", "cabin", "flight", "dispatch",
    "etihad", "emirates", "qatar", "flydubai", "air arabia", "turkish", "iata",
    "seravia", "station", "travel", "ticketing", "gds", "hospitality",
)

TECH_KEYWORDS = (
    "software", "developer", "engineer", "python", "ai", "ml", "data", "cyber",
    "security", "soc", "analyst", "eval", "evaluator", "prompt", "it ", "support",
    "devops", "cloud", "linux", "automation", "remote", "qa", "tester",
)


def resolve_vault_dir(vault_dir: Optional[str] = None) -> Path:
    """Resolves the active Aethelgard Obsidian Vault directory."""
    if vault_dir:
        return Path(os.path.abspath(os.path.expanduser(vault_dir)))
    for env_key in ("VAULT_DIR", "WIKI_PATH", "OBSIDIAN_VAULT_PATH"):
        val = os.environ.get(env_key)
        if val:
            return Path(os.path.abspath(os.path.expanduser(val)))
    return Path(reel_and_url_ingest.get_default_vault_path())


def _collect_unique_files(candidate_dirs: List[Path], extensions: tuple) -> List[Path]:
    """Collects sorted existing files from prioritized directories without filename duplicates."""
    seen: Dict[str, Path] = {}
    for directory in candidate_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for item in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if item.is_file() and not item.name.startswith("~$") and item.suffix.lower() in extensions:
                seen.setdefault(item.name, item)
    return list(seen.values())


def _pick_primary_cv(cvs: List[Path]) -> Optional[Path]:
    """Selects the highest-priority CV file from discovered CVs."""
    if not cvs:
        return None
    by_name = {p.name.lower(): p for p in cvs}
    for preferred in CV_PRIORITY_ORDER:
        match = by_name.get(preferred.lower())
        if match:
            return match
    pdf_cvs = [p for p in cvs if p.suffix.lower() == ".pdf"]
    return pdf_cvs[0] if pdf_cvs else cvs[0]


def locate_career_assets(vault_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Discovers Omar's CVs, Etihad Airways L&D certificates, and Obsidian Vault
    career markdown notes across Vault, ~/.aethelgard/career_assets, and repo profile.
    """
    vault_path = resolve_vault_dir(vault_dir)
    aethelgard_assets = Path(os.path.expanduser("~/.aethelgard/career_assets"))
    repo_assets = SCRIPT_DIR / "profile" / "career_assets"

    cv_dirs = [
        vault_path / "raw" / "documents" / "cv",
        aethelgard_assets / "cv",
        repo_assets / "cv",
        Path(os.path.expanduser("~/Desktop/CVs")),
    ]
    cert_dirs = [
        vault_path / "raw" / "documents" / "etihad_training" / "certificates",
        aethelgard_assets / "certificates",
        repo_assets / "certificates",
    ]

    cvs = _collect_unique_files(cv_dirs, (".pdf", ".docx"))
    certs = sorted(
        _collect_unique_files(cert_dirs, (".pdf", ".png", ".jpg", ".jpeg")),
        key=lambda p: p.name.lower(),
    )
    primary_cv = _pick_primary_cv(cvs)

    profile_note_relpaths = [
        "00 Profile.md",
        "01 Technical Skills.md",
        "03 Work Experience.md",
        "professional_work_history.md",
        "entities/etihad-airways-station-operations.md",
    ]
    vault_notes = [
        str(vault_path / rel)
        for rel in profile_note_relpaths
        if (vault_path / rel).is_file()
    ]

    return {
        "vault_dir": str(vault_path),
        "primary_cv": str(primary_cv) if primary_cv else None,
        "all_cvs": [str(p) for p in cvs],
        "certificates": [str(p) for p in certs],
        "certificate_count": len(certs),
        "cv_count": len(cvs),
        "vault_profile_notes": vault_notes,
        "identity": OMAR_IDENTITY,
    }


def load_vault_profile_summary(vault_dir: Optional[str] = None, max_chars: int = 3500) -> str:
    """Reads concise verified snippets from Omar's Vault profile notes for context."""
    vault_path = resolve_vault_dir(vault_dir)
    targets = [
        vault_path / "00 Profile.md",
        vault_path / "03 Work Experience.md",
        vault_path / "professional_work_history.md",
        vault_path / "01 Technical Skills.md",
    ]
    chunks: List[str] = []
    remaining = max_chars
    for note in targets:
        if not note.is_file() or remaining <= 120:
            continue
        try:
            text = note.read_text(encoding="utf-8", errors="ignore").strip()
            snippet = text[: min(900, remaining)]
            chunks.append(f"### {note.name}\n{snippet}")
            remaining -= len(snippet)
        except Exception:
            continue

    if not chunks:
        return (
            f"{OMAR_IDENTITY['name']} | {OMAR_IDENTITY['location']} | "
            f"{OMAR_IDENTITY['email']} | {OMAR_IDENTITY['phone']}\n"
            + "\n".join(OMAR_IDENTITY["core_highlights"]["aviation"])
            + "\n"
            + "\n".join(OMAR_IDENTITY["core_highlights"]["tech"])
        )
    return "\n\n".join(chunks)


def ingest_job_posting_url(url: str) -> Dict[str, Any]:
    """Fetches a job posting URL cleanly via reel_and_url_ingest without headless browsers."""
    if not url or not url.strip():
        return {"success": False, "text": "", "title": ""}
    try:
        meta = reel_and_url_ingest.extract_media_content(url.strip(), transcribe=False)
        title = str(meta.get("title") or "").strip()
        text = str(
            meta.get("transcript")
            or meta.get("description")
            or meta.get("content")
            or ""
        ).strip()
        return {
            "success": bool(meta.get("success", True)),
            "title": title,
            "text": text[:3000],
            "url": meta.get("canonical_url") or url.strip(),
        }
    except Exception as e:
        return {"success": False, "title": "", "text": "", "url": url.strip(), "error": str(e)}


def _classify_role_track(role: str, job_description: str) -> str:
    """Classifies the job track into 'aviation', 'tech', or 'hybrid'."""
    haystack = f"{role} {job_description}".lower()
    is_av = any(kw in haystack for kw in AVIATION_KEYWORDS)
    is_tech = any(kw in haystack for kw in TECH_KEYWORDS)
    if is_av and is_tech:
        return "hybrid"
    if is_av:
        return "aviation"
    if is_tech:
        return "tech"
    return "hybrid"


def generate_tailored_application(
    company: str,
    role: str,
    job_description: str = "",
    job_url: str = "",
    recipient: str = "",
    channel: str = "gmail",
    include_certificates: bool = True,
    attach_all_cvs: bool = False,
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates a tailored cover email, concise recruiter outreach note, and attachment
    list grounded strictly in Omar Elnemr's verified Vault history and career assets.
    """
    assets = locate_career_assets(vault_dir=vault_dir)
    track = _classify_role_track(role, job_description)

    if track == "aviation":
        bullet_pool = OMAR_IDENTITY["core_highlights"]["aviation"] + OMAR_IDENTITY["core_highlights"]["tech"][:1]
    elif track == "tech":
        bullet_pool = OMAR_IDENTITY["core_highlights"]["tech"] + OMAR_IDENTITY["core_highlights"]["aviation"][:1]
    else:
        bullet_pool = OMAR_IDENTITY["core_highlights"]["aviation"][:2] + OMAR_IDENTITY["core_highlights"]["tech"][:2]

    attachments: List[str] = []
    if attach_all_cvs:
        attachments.extend(assets["all_cvs"])
    elif assets.get("primary_cv"):
        attachments.append(assets["primary_cv"])

    if include_certificates or track in ("aviation", "hybrid"):
        for cert in assets["certificates"]:
            if cert not in attachments:
                attachments.append(cert)

    subject = f"Application for {role} — Omar Elnemr"
    ref_line = f"Regarding: {role} at {company}" + (f" ({job_url})" if job_url else "")
    att_note = (
        f"Attached: {', '.join(Path(p).name for p in attachments)}"
        if attachments
        else "CV and supporting certifications available immediately upon request."
    )

    body_lines = [
        f"Dear {company} Hiring Team,",
        "",
        f"I am writing to express my strong interest in the {role} position at {company}.",
        f"({ref_line})" if job_url else "",
        "",
        "With hands-on airport ground operations experience, enterprise AI/software evaluation, and a Computer Science & Cybersecurity academic background, I bring immediate operational reliability:",
        "",
    ]
    for b in bullet_pool:
        body_lines.append(f"• {b}")

    body_lines.extend([
        "",
        "Credentials & Education:",
        f"• Languages: {', '.join(OMAR_IDENTITY['languages'])}",
        f"• Education: {OMAR_IDENTITY['education'][0]}; {OMAR_IDENTITY['education'][2]}",
        f"• {att_note}",
        "",
        "I would welcome the opportunity to contribute to your team and discuss how my background aligns with your operational goals.",
        "",
        "Best regards,",
        OMAR_IDENTITY["name"],
        f"Email: {OMAR_IDENTITY['email']} | Phone: {OMAR_IDENTITY['phone']}",
        f"Location: {OMAR_IDENTITY['location']}",
        f"LinkedIn: {OMAR_IDENTITY['linkedin']}",
    ])
    body = "\n".join(line for line in body_lines if line is not None).replace("\n\n\n", "\n\n")

    short_pitch = (
        f"Hello {company} team — I'm Omar Elnemr, applying for {role}. "
        f"I bring Amadeus Altéa CM (91%) & Etihad L&D airport operations experience alongside CS/Cybersecurity and AI evaluation credentials. "
        f"CV ready ({OMAR_IDENTITY['email']} | {OMAR_IDENTITY['phone']})."
    )

    return {
        "company": company,
        "role": role,
        "track": track,
        "channel": channel.lower(),
        "recipient": recipient,
        "job_url": job_url,
        "subject": subject,
        "body": body,
        "short_pitch": short_pitch,
        "attachments": attachments,
        "vault_dir": assets["vault_dir"],
    }


def _log_application_to_vault(entry: Dict[str, Any], vault_dir: Optional[str] = None) -> Dict[str, Any]:
    """Appends a staged application to $VAULT_DIR/career/job_applications_tracker.md and log.md."""
    vault_path = resolve_vault_dir(vault_dir)
    if not vault_path.exists():
        vault_path.mkdir(parents=True, exist_ok=True)

    career_dir = vault_path / "career"
    career_dir.mkdir(parents=True, exist_ok=True)
    tracker_path = career_dir / "job_applications_tracker.md"

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    company_safe = entry["company"].replace("|", "/")
    role_safe = entry["role"].replace("|", "/")
    channel_safe = entry["channel"]
    target_safe = (entry.get("recipient") or entry.get("job_url") or "Portal / TBD").replace("|", "/")
    draft_id = entry["draft_id"]
    att_names = ", ".join(Path(a).name for a in entry.get("attachments", [])) or "None"

    if not tracker_path.exists():
        header = (
            "---\n"
            "title: Job Applications Tracker\n"
            "type: career-tracker\n"
            "tags: [career, job-applications, aethelgard]\n"
            "---\n\n"
            "# Job Applications Tracker\n\n"
            "> Automatically updated by `job_application_engine.py`. Never modifies files under `raw/`.\n\n"
            "| Date | Company | Role | Channel | Target | Draft ID | Attachments | Status |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        )
        tracker_path.write_text(header, encoding="utf-8")

    row = f"| {today} | {company_safe} | {role_safe} | {channel_safe} | {target_safe} | `{draft_id}` | {att_names} | pending_approval |\n"
    with open(tracker_path, "a", encoding="utf-8") as tf:
        tf.write(row)

    log_path = vault_path / "log.md"
    log_entry = (
        f"\n## [{today}] career | Staged Job Application: {role_safe} at {company_safe} (`{draft_id}`)\n"
        f"- **Channel / Target:** `{channel_safe}` -> `{target_safe}`\n"
        f"- **Attachments:** {att_names}\n"
        f"- **Tracker:** `career/job_applications_tracker.md`\n"
    )
    if log_path.exists():
        existing_log = log_path.read_text(encoding="utf-8", errors="ignore")
        marker = "> Format: `## [YYYY-MM-DD] action | subject`"
        if marker in existing_log:
            parts = existing_log.split(marker, 1)
            log_path.write_text(parts[0] + marker + "\n" + log_entry + parts[1], encoding="utf-8")
        else:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write("\n" + log_entry)
    else:
        log_path.write_text(f"# Aethelgard Vault — Wiki Log\n{log_entry}", encoding="utf-8")

    return {"tracker_path": str(tracker_path), "log_path": str(log_path)}


def stage_job_application(
    company: str,
    role: str,
    to: str = "",
    channel: str = "gmail",
    job_url: str = "",
    job_description: str = "",
    include_certificates: bool = True,
    attach_all_cvs: bool = False,
    notify_telegram: bool = False,
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    End-to-end job application workflow: ingests URL if provided, tailors cover
    material from Omar's Vault profile, stages the outbound draft with CV/certificate
    attachments via Approval Gatekeeper, and logs to Vault tracker + log.md.
    """
    merged_desc = job_description.strip()
    url_info: Dict[str, Any] = {}
    if job_url and not merged_desc:
        url_info = ingest_job_posting_url(job_url)
        if url_info.get("text"):
            merged_desc = f"{url_info.get('title', '')}\n{url_info['text']}".strip()

    norm_channel = channel.strip().lower()
    if norm_channel in ("email", "google"):
        norm_channel = "gmail"
    elif norm_channel == "telegram":
        norm_channel = "telegram_user"

    if norm_channel not in approval_gatekeeper.SUPPORTED_PLATFORMS:
        raise ValueError(
            f"Unsupported job application channel '{channel}'. "
            f"Use one of: gmail, whatsapp, telegram_user, linkedin"
        )

    app_pkg = generate_tailored_application(
        company=company,
        role=role,
        job_description=merged_desc,
        job_url=job_url,
        recipient=to,
        channel=norm_channel,
        include_certificates=include_certificates,
        attach_all_cvs=attach_all_cvs,
        vault_dir=vault_dir,
    )

    target = to.strip() or job_url.strip() or f"{company} Careers"
    action = "send_email" if norm_channel == "gmail" else "send_message"
    content = app_pkg["body"] if norm_channel == "gmail" else app_pkg["short_pitch"]

    draft = approval_gatekeeper.stage_draft(
        platform=norm_channel,
        action=action,
        recipient_or_target=target,
        content=content,
        metadata={
            "subject": app_pkg["subject"],
            "company": company,
            "role": role,
            "job_url": job_url,
            "track": app_pkg["track"],
            "attachments": app_pkg["attachments"],
        },
        subject=app_pkg["subject"] if norm_channel == "gmail" else None,
        notify_telegram=notify_telegram,
    )

    vault_logs = _log_application_to_vault(
        {
            "company": company,
            "role": role,
            "channel": norm_channel,
            "recipient": target,
            "job_url": job_url,
            "draft_id": draft["draft_id"],
            "attachments": app_pkg["attachments"],
        },
        vault_dir=vault_dir,
    )

    return {
        "success": True,
        "draft_id": draft["draft_id"],
        "status": draft["status"],
        "platform": norm_channel,
        "recipient": target,
        "subject": app_pkg["subject"],
        "attachments": app_pkg["attachments"],
        "attachment_names": [Path(p).name for p in app_pkg["attachments"]],
        "approval_card": approval_gatekeeper.format_approval_card(draft),
        "tracker_path": vault_logs["tracker_path"],
        "log_path": vault_logs["log_path"],
        "url_ingested": bool(url_info.get("success")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aethelgard Autonomous Job Application & Career Engine")
    parser.add_argument("--assets", action="store_true", help="List discovered CV and certificate files")
    parser.add_argument("--profile", action="store_true", help="Print loaded career profile from Vault")
    parser.add_argument("--apply", action="store_true", help="Tailor and stage a job application draft")
    parser.add_argument("--company", default="", help="Target company or airline name")
    parser.add_argument("--role", default="", help="Target role title")
    parser.add_argument("--to", default="", help="Recipient hiring email, WhatsApp number, or profile handle")
    parser.add_argument("--channel", default="gmail", help="Outbound channel: gmail, whatsapp, telegram_user, linkedin")
    parser.add_argument("--url", default="", help="Job posting URL to fetch and parse")
    parser.add_argument("--desc", default="", help="Optional pasted job description text")
    parser.add_argument("--no-certs", action="store_true", help="Attach only CV without Etihad certificates")
    parser.add_argument("--all-cvs", action="store_true", help="Attach all available CV variants")
    parser.add_argument("--notify", action="store_true", help="Send approval card directly to Omar on Telegram")
    args = parser.parse_args()

    if args.assets:
        print(json.dumps(locate_career_assets(), indent=2, ensure_ascii=False))
        return 0

    if args.profile:
        payload = {
            "identity": OMAR_IDENTITY,
            "assets": locate_career_assets(),
            "vault_summary": load_vault_profile_summary(),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.apply:
        if not args.company or not args.role:
            print(json.dumps({"success": False, "error": "--company and --role are required with --apply"}))
            return 2
        res = stage_job_application(
            company=args.company,
            role=args.role,
            to=args.to,
            channel=args.channel,
            job_url=args.url,
            job_description=args.desc,
            include_certificates=not args.no_certs,
            attach_all_cvs=args.all_cvs,
            notify_telegram=args.notify,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
