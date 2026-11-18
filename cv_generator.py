#!/usr/bin/env python3
"""
Aethelgard Dynamic ATS CV Generator (cv_generator.py)
=====================================================
Generates clean, 1-page, ATS-compliant PDF resumes tailored to BOTH:
  1. Target Niche Track:
     - `aviation`       : Major Airlines (Etihad, Qatar Airways, Air Arabia, Emirates, flydubai, etc.),
                          Airport Passenger/Ground Ops, VIP Service, Amadeus Altéa CM, GDS Ticketing.
     - `remote_support` : Bilingual (Arabic/English) Technical & Customer Support, Operations,
                          Trust & Safety, QA / AI Data Evaluation.
     - `tech_junior`    : IT Support Engineer, Service Desk / Helpdesk, Junior Cybersecurity / SOC,
                          Linux/SQL/Python Technical Operations.
  2. Specific Job Offer (Company + Role + verified JD keyword alignment — strictly grounded in
     Omar Elnemr's real experience; NEVER fabricates unverified skills or employers).

Outputs 1-page Letter PDFs via ReportLab to `~/.aethelgard/career_assets/tailored_cvs/`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


CONTACT_ITEMS: List[Tuple[str, Optional[str]]] = [
    ("Damascus, Syria (Open to Relocation / Remote)", None),
    ("+963 934 694 750", None),
    ("omar.alnemr04@gmail.com", "mailto:omar.alnemr04@gmail.com"),
    ("linkedin.com/in/omar-el-nemr", "https://www.linkedin.com/in/omar-el-nemr"),
]

# Verified skills only — used to prioritize emphasis when matching a specific JD
VERIFIED_KEYWORD_HIGHLIGHTS: Dict[str, str] = {
    "amadeus": "Amadeus Altéa CM (91% Certified)",
    "altea": "Amadeus Altéa Customer Management",
    "altéa": "Amadeus Altéa Customer Management",
    "vip": "VIP / CIP Guest Handling",
    "irops": "Irregular Operations (IROPS) & Disruption Recovery",
    "check-in": "Check-In, Boarding & DCS Operations",
    "arabic": "Bilingual Arabic (Native) & English (Fluent)",
    "gds": "Airline GDS Ticketing & Reservations",
    "linux": "Linux (Ubuntu/systemd) Administration",
    "sql": "SQL / SQLite Diagnostics",
    "python": "Python Troubleshooting & Automation",
    "cyber": "Google Cybersecurity Certified (IAM, Log Triage)",
    "soc": "Security Monitoring & Incident Triage",
    "qa": "Technical QA & Rubric Evaluation",
    "sla": "SLA-Driven Ticket Resolution & Escalation",
    "crm": "CRM & Omnichannel Customer Operations",
}

NICHE_PROFILES: Dict[str, Dict[str, Any]] = {
    "aviation": {
        "default_headline": "Airport Passenger & Ground Operations Specialist | Amadeus Altéa CM Certified",
        "summary_template": (
            "Bilingual (Native Arabic / Fluent English) Airport Passenger & Ground Operations professional "
            "targeting the {role} role at {company}. Experienced at Damascus International Airport (DAM/OSDI) "
            "handling Etihad Airways, Turkish Airlines, flydubai, Air Arabia, and Syrian Airlines across check-in, "
            "boarding gates, VIP assistance, baggage coordination, and irregular operations (IROPS). Certified "
            "by Etihad Airways L&D in Amadeus Altéa Customer Management (32 Hrs, 91% score) and Airport Service Delivery."
        ),
        "skills": [
            ("DCS & Airline Systems", "Amadeus Altéa Customer Management (CM — 91%), Departure Control Systems (DCS), Check-In, Boarding Gates, Baggage & Ramp Coordination"),
            ("Passenger & VIP Service", "VIP / CIP Guest Handling, Irregular Operations (IROPS) & Disruption Recovery, De-Escalation, IATA & Regulatory Travel Document Verification"),
            ("Travel & GDS Operations", "Reservations, Flight Rebooking, Fare Rules, Travel Agency Ticketing, Manifest & Load Coordination"),
            ("Technical & Reporting", "Computer Science Track (UoPeople / SVU), Operational Reporting (Excel/CSV), CRM Systems, Rapid System Troubleshooting"),
        ],
        "experience": [
            {
                "title": "Airport Passenger & Ground Operations Agent — SERAVIA",
                "location": "Damascus International Airport (DAM / OSDI)",
                "dates": "Aug 2026 – Nov 2026",
                "bullets": [
                    "Executed full passenger handling (check-in, seat allocation, boarding control, and arrival assistance) for Etihad Airways (EY), Turkish Airlines (TK), flydubai (FZ), Air Arabia (G9), and Syrian Airlines (RB).",
                    "Operated Amadeus Altéa Customer Management and airline DCS terminals under high-volume turnaround deadlines with strict compliance on visas, travel documents, and IATA baggage policies.",
                    "Managed VIP/CIP guests and resolved Irregular Operations (IROPS), missed connections, and flight disruptions calmly to protect On-Time Performance (OTP) and guest satisfaction.",
                    "Coordinated directly with ramp, baggage services, load control, and cabin crew to close flights accurately and safely.",
                ],
            },
            {
                "title": "Travel & Ticketing Coordinator (GDS / Reservations) — Hope Road, El Synable & Nour Aldeen Travel",
                "location": "Damascus, Syria",
                "dates": "2024 – 2026",
                "bullets": [
                    "Handled end-to-end flight reservations, itinerary modifications, fare calculations, and reissues for regional GCC and international travelers.",
                    "Advised passengers on airline baggage allowances, transit visa requirements, and schedule changes to minimize airport-day disruptions.",
                ],
            },
            {
                "title": "Bilingual Customer Operations & Dispatch Specialist — Movo Delivery & Intelcia / Optimum",
                "location": "Damascus, Syria / Remote",
                "dates": "2023 – 2025",
                "bullets": [
                    "Delivered high-CSAT bilingual (Arabic/English) customer support, live escalation resolution, and real-time operational dispatch quality assurance.",
                    "Maintained strict SLA response times and clear documentation across multi-channel customer inquiries.",
                ],
            },
        ],
        "certifications": [
            "Etihad Airways L&D: Amadeus Altéa Customer Management (CM) 32 Hrs — 91% Score (Oct 2026)",
            "Etihad Airways L&D: Introduction to Ground Services (IGS 16 Hrs) & INSPIRE Airport Service Delivery (16 Hrs)",
            "Etihad Airways L&D: Airport Disruption Management Workshop & AUG Edition 2026-GSU-34",
            "Google Cybersecurity Professional Certificate (2025)",
        ],
        "education": [
            ("B.Sc. in Computer Science (In Progress)", "University of the People, California", "Expected 2027"),
            ("Diploma in Computer Science / Software Engineering", "Syrian Virtual University (SVU)", "2025"),
        ],
    },
    "remote_support": {
        "default_headline": "Bilingual Technical Customer Support & Operations Specialist (Arabic / English)",
        "summary_template": (
            "Bilingual (Native Arabic / Fluent English) Technical Customer Support & Operations Specialist "
            "applying for {role} at {company}. Combines hands-on high-pressure customer resolution, SLA-driven "
            "omnichannel support (Intelcia/Optimum, Movo Delivery, Etihad/SERAVIA VIP operations), and technical "
            "QA / AI evaluation (Alignerr, Outlier, RWS) with Computer Science & Google Cybersecurity credentials."
        ),
        "skills": [
            ("Customer & Technical Support", "L1/L2 Troubleshooting, Bilingual Arabic/English Chat, Email & Voice Support, SLA & KPI Ownership, Escalation & De-Escalation"),
            ("Operations & QA", "Live Dispatch Coordination, Quality Assurance Audits, SOP Documentation, Technical Rubric Evaluation, Root Cause Triage"),
            ("Technical Stack", "HTML5/CSS3/JavaScript, Python, SQL/SQLite, Linux (Ubuntu), REST APIs, HTTP/DNS Diagnostics, Git/GitHub, CRM & Ticketing Workflows"),
            ("AI & Productivity", "LLM Evaluation & Prompt Engineering (RLHF), Excel/Sheets Reporting, Cross-Timezone Remote Collaboration"),
        ],
        "experience": [
            {
                "title": "AI Data Evaluator & Technical QA Specialist — Alignerr, Outlier, RWS & DataForce",
                "location": "Remote (Global)",
                "dates": "Jan 2025 – Present",
                "bullets": [
                    "Audit and evaluate complex technical outputs across Python, SQL, JavaScript, and bilingual English/Arabic workflows against strict quality rubrics.",
                    "Identify edge-case failures, factual inaccuracies, and UX/workflow defects with clear written root-cause documentation.",
                    "Consistently meet tight remote turnaround targets and high inter-annotator accuracy benchmarks across global distributed teams.",
                ],
            },
            {
                "title": "VIP Guest Experience & Operations Agent — SERAVIA (Etihad Airways, Turkish Airlines, flydubai)",
                "location": "Damascus International Airport (DAM)",
                "dates": "Aug 2026 – Nov 2026",
                "bullets": [
                    "Resolved high-urgency passenger inquiries, schedule disruptions (IROPS), and VIP service requests under strict time-critical operational SLAs.",
                    "Certified in Amadeus Altéa Customer Management (91% score) and INSPIRE Service Delivery by Etihad Airways L&D.",
                ],
            },
            {
                "title": "Bilingual Customer Support & Operations Dispatch — Movo Delivery & Intelcia / Optimum",
                "location": "Remote / Damascus",
                "dates": "2023 – 2025",
                "bullets": [
                    "Managed high-volume bilingual customer tickets across phone, chat, and email—diagnosing service issues, tracking orders, and driving first-contact resolution.",
                    "Coordinated real-time dispatch logistics and QA reviews to reduce repeat escalations and improve customer satisfaction.",
                ],
            },
        ],
        "certifications": [
            "Google Cybersecurity Professional Certificate (2025)",
            "Google Foundations of User Experience (UX) Design (2024)",
            "Etihad Airways L&D: INSPIRE Service Delivery & Amadeus Altéa CM (91% Score)",
        ],
        "education": [
            ("B.Sc. in Computer Science (In Progress)", "University of the People, California", "Expected 2027"),
            ("Diploma in Computer Science", "Syrian Virtual University (SVU)", "2025"),
        ],
    },
    "tech_junior": {
        "default_headline": "IT Support Engineer & Cybersecurity-Trained Computer Science Specialist",
        "summary_template": (
            "Computer Science student and Google Cybersecurity-certified technical specialist targeting the "
            "{role} position at {company}. Hands-on experience administering Linux cloud VMs (Ubuntu, systemd), "
            "diagnosing network/API/SQL issues, automating workflows in Python/Bash, and performing technical "
            "code & QA reviews (Alignerr). Bilingual (Native Arabic / Fluent English) with strong L1/L2 support discipline."
        ),
        "skills": [
            ("IT Support & Systems", "Linux (Ubuntu), Windows, systemd Service Management, SSH, Process & Log Triage, Root Cause Analysis (RCA), L1/L2 Helpdesk"),
            ("Networking & Security", "TCP/IP, DNS, HTTP/HTTPS, OAuth 2.0, IAM Concepts, Wireshark, Nmap, Incident Response Fundamentals (Google Cybersecurity)"),
            ("Programming & Data", "Python, SQL / SQLite (Schema & Query Troubleshooting), JavaScript (ES6+), HTML5/CSS3, Bash/PowerShell, Git/GitHub, REST APIs"),
            ("Enterprise & Operations", "Amadeus Altéa Enterprise Terminals (91% Certified), Technical Documentation, Bilingual User Support & Escalation Handling"),
        ],
        "experience": [
            {
                "title": "Linux Cloud & AI Systems Developer (Independent Project — Aethelgard)",
                "location": "Remote / Damascus",
                "dates": "2025 – Present",
                "bullets": [
                    "Deploy and maintain a 24/7 production Python/Node.js service stack on a Google Cloud Linux VM managed via systemd with automated failover and health monitoring.",
                    "Troubleshoot real-world networking, TLS/HTTP, OAuth 2.0 token refresh, and SQLite state persistence issues from system logs (`journalctl`).",
                    "Built automated diagnostics, encrypted local credential storage, and clean CLI/web tooling published on GitHub.",
                ],
            },
            {
                "title": "Technical Code & AI Evaluator — Alignerr, Outlier & RWS",
                "location": "Remote",
                "dates": "Jan 2025 – Present",
                "bullets": [
                    "Review and debug Python, SQL, JavaScript, and Bash solutions for logical accuracy, security weaknesses, and edge-case reliability.",
                    "Write concise technical bug reports and structured QA evaluations across English and Arabic engineering tasks.",
                ],
            },
            {
                "title": "Enterprise Airport Systems & Support Operations — SERAVIA / Movo Delivery / Intelcia",
                "location": "Damascus / Remote",
                "dates": "2023 – Nov 2026",
                "bullets": [
                    "Operated mission-critical airline DCS terminals (Amadeus Altéa CM, 91% score) for Etihad Airways, Turkish Airlines, flydubai, and Air Arabia under zero-downtime flight schedules.",
                    "Delivered SLA-driven bilingual L1 technical & customer operations support with calm escalation handling.",
                ],
            },
        ],
        "certifications": [
            "Google Cybersecurity Professional Certificate (2025)",
            "Google Foundations of User Experience (UX) Design (2024)",
            "Etihad Airways L&D: Amadeus Altéa Customer Management (CM) — 91% Score",
        ],
        "education": [
            ("B.Sc. in Computer Science (In Progress)", "University of the People, California", "Expected 2027"),
            ("Diploma in Computer Science", "Syrian Virtual University (SVU)", "2025"),
        ],
    },
}


def _safe_slug(text: str, max_len: int = 28) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip()).strip("_")
    return cleaned[:max_len] or "Role"


def _xml_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _normalize_track(track: str, role: str = "", job_description: str = "") -> str:
    t = (track or "").strip().lower()
    if t in NICHE_PROFILES:
        return t
    hay = f"{role} {job_description}".lower()
    if any(k in hay for k in ("airline", "airport", "aviation", "etihad", "qatar", "arabia", "emirates", "flydubai", "dnata", "cabin", "ground", "passenger", "amadeus", "vip service", "flight", "station")):
        return "aviation"
    if any(k in hay for k in ("it support", "helpdesk", "help desk", "service desk", "cyber", "soc ", "network", "linux", "sysadmin", "engineer", "developer", "security")):
        return "tech_junior"
    return "remote_support"


def _extract_verified_jd_highlights(job_description: str) -> List[str]:
    low = (job_description or "").lower()
    hits: List[str] = []
    seen = set()
    for kw, label in VERIFIED_KEYWORD_HIGHLIGHTS.items():
        if kw in low and label not in seen:
            seen.add(label)
            hits.append(label)
    return hits[:4]


def get_tailored_cv_dir() -> Path:
    base = Path(os.environ.get("AETHELGARD_HOME", os.path.expanduser("~/.aethelgard")))
    d = base / "career_assets" / "tailored_cvs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_tailored_cv(
    company: str,
    role: str,
    track: str = "",
    job_description: str = "",
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds a tailored, 1-page ATS PDF resume for a specific company & role, anchored on
    the best-fitting niche profile (`aviation`, `remote_support`, or `tech_junior`).
    """
    norm_track = _normalize_track(track, role=role, job_description=job_description)
    profile = NICHE_PROFILES[norm_track]

    clean_company = (company or "Target Organization").strip()
    clean_role = (role or profile["default_headline"]).strip()
    # Keep filename compact and ATS-friendly
    if output_path:
        pdf_path = Path(os.path.abspath(os.path.expanduser(output_path)))
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fname = f"Omar_Elnemr_{_safe_slug(clean_company, 20)}_{_safe_slug(clean_role, 24)}_CV.pdf"
        pdf_path = get_tailored_cv_dir() / fname

    headline = f"{clean_role}  |  {profile['default_headline'].split('|')[-1].strip()}" if len(clean_role) <= 42 else profile["default_headline"]
    summary = profile["summary_template"].format(role=clean_role, company=clean_company)
    jd_matches = _extract_verified_jd_highlights(job_description)
    if jd_matches:
        summary += f" Core alignment for {clean_company}: {', '.join(jd_matches)}."

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=LETTER,
        leftMargin=0.48 * inch,
        rightMargin=0.48 * inch,
        topMargin=0.36 * inch,
        bottomMargin=0.32 * inch,
        title=f"Omar Elnemr - {clean_role} ({clean_company})",
        author="Omar Elnemr",
    )

    DARK = colors.HexColor("#111827")
    NAVY = colors.HexColor("#1E3A5F")
    MUTED = colors.HexColor("#4B5563")
    LINE = colors.HexColor("#CBD5E1")

    s_name = ParagraphStyle("Name", fontName="Helvetica-Bold", fontSize=17.5, leading=20, alignment=TA_CENTER, textColor=DARK, spaceAfter=2)
    s_headline = ParagraphStyle("Headline", fontName="Helvetica-Bold", fontSize=9.8, leading=12.5, alignment=TA_CENTER, textColor=NAVY, spaceAfter=3)
    s_contact = ParagraphStyle("Contact", fontName="Helvetica", fontSize=8.4, leading=11, alignment=TA_CENTER, textColor=MUTED, spaceAfter=4)
    s_section = ParagraphStyle("Section", fontName="Helvetica-Bold", fontSize=9.6, leading=11.8, textColor=NAVY, spaceBefore=5.5, spaceAfter=2)
    s_body = ParagraphStyle("Body", fontName="Helvetica", fontSize=8.7, leading=11.5, textColor=DARK, spaceAfter=2)
    s_skill = ParagraphStyle("Skill", fontName="Helvetica", fontSize=8.5, leading=11.2, textColor=DARK, spaceAfter=1.6)
    s_role_left = ParagraphStyle("RoleLeft", fontName="Helvetica-Bold", fontSize=9.0, leading=11.5, textColor=DARK)
    s_role_right = ParagraphStyle("RoleRight", fontName="Helvetica-Bold", fontSize=8.4, leading=11.5, alignment=TA_RIGHT, textColor=MUTED)
    s_sub = ParagraphStyle("Sub", fontName="Helvetica-Oblique", fontSize=8.3, leading=10.6, textColor=MUTED, spaceAfter=1.8)
    s_bullet = ParagraphStyle("Bullet", fontName="Helvetica", fontSize=8.5, leading=11.2, textColor=DARK, leftIndent=11, firstLineIndent=-8, spaceAfter=1.6)

    usable_w = LETTER[0] - doc.leftMargin - doc.rightMargin
    story = []

    story.append(Paragraph("OMAR ELNEMR", s_name))
    story.append(Paragraph(_xml_escape(headline), s_headline))
    contact_parts = [
        f'<a href="{url}" color="#1E3A5F">{_xml_escape(txt)}</a>' if url else _xml_escape(txt)
        for txt, url in CONTACT_ITEMS
    ]
    story.append(Paragraph("  |  ".join(contact_parts), s_contact))
    story.append(HRFlowable(width="100%", thickness=1.0, color=NAVY, spaceBefore=1, spaceAfter=4))

    def add_section(title: str):
        story.append(Paragraph(title, s_section))
        story.append(HRFlowable(width="100%", thickness=0.6, color=LINE, spaceBefore=0, spaceAfter=3))

    add_section("PROFESSIONAL SUMMARY")
    story.append(Paragraph(_xml_escape(summary), s_body))

    add_section("CORE COMPETENCIES")
    for cat, vals in profile["skills"]:
        story.append(Paragraph(f"<b>{_xml_escape(cat)}:</b> {_xml_escape(vals)}", s_skill))

    add_section("RELEVANT EXPERIENCE")
    for idx, exp in enumerate(profile["experience"]):
        tbl = Table(
            [[Paragraph(_xml_escape(exp["title"]), s_role_left), Paragraph(_xml_escape(exp["dates"]), s_role_right)]],
            colWidths=[usable_w * 0.78, usable_w * 0.22],
        )
        tbl.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1 if idx > 0 else 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
        story.append(Paragraph(_xml_escape(exp["location"]), s_sub))
        for b in exp["bullets"]:
            story.append(Paragraph(f"•  {_xml_escape(b)}", s_bullet))
        if idx < len(profile["experience"]) - 1:
            story.append(Spacer(1, 2.5))

    add_section("CERTIFICATIONS & TRAINING")
    for cert in profile["certifications"]:
        story.append(Paragraph(f"•  {_xml_escape(cert)}", s_bullet))

    add_section("EDUCATION & LANGUAGES")
    for deg, school, dates in profile["education"]:
        tbl = Table(
            [[Paragraph(f"<b>{_xml_escape(deg)}</b> — {_xml_escape(school)}", s_body), Paragraph(_xml_escape(dates), s_role_right)]],
            colWidths=[usable_w * 0.78, usable_w * 0.22],
        )
        tbl.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
    story.append(Paragraph("<b>Languages:</b>  Arabic (Native)  |  English (Fluent / Full Professional Proficiency)", s_skill))

    doc.build(story)

    return {
        "success": True,
        "pdf_path": str(pdf_path),
        "filename": pdf_path.name,
        "track": norm_track,
        "company": clean_company,
        "role": clean_role,
        "jd_highlights": jd_matches,
        "size_bytes": pdf_path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a tailored 1-page ATS CV PDF for Omar Elnemr.")
    parser.add_argument("--company", required=True, help="Target company or airline name")
    parser.add_argument("--role", required=True, help="Target role title")
    parser.add_argument("--track", default="", help="Optional track override: aviation, remote_support, tech_junior")
    parser.add_argument("--desc", default="", help="Optional job description snippet for verified keyword alignment")
    parser.add_argument("--out", default="", help="Optional explicit output PDF path")
    args = parser.parse_args()

    res = generate_tailored_cv(
        company=args.company,
        role=args.role,
        track=args.track,
        job_description=args.desc,
        output_path=args.out or None,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
