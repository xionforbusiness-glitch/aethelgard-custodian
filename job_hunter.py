#!/usr/bin/env python3
"""
Aethelgard Firecrawl Job Hunter & Safe-Batch Direct-Email Pipeline (job_hunter.py)
==================================================================================
Two-pillar career pipeline tailored to Omar Elnemr's verified profile:
  1. No-Account Job Discovery & Ranking (Firecrawl Search + Public ATS/Remote APIs):
     - Hunts fresh openings across no-account ATS portals (Lever, Greenhouse, Ashby,
       Workable, SmartRecruiters) plus direct HR-email postings across:
         * aviation       (UAE/GCC/International ground ops, passenger service, Amadeus/GDS)
         * remote_support (Global/EMEA remote bilingual Arabic/English support, ops, AI eval/QA)
         * tech_junior    (Remote/GCC junior IT support, helpdesk, SOC/cybersecurity, QA)
     - Filters out Emiratisation / UAE-National-only / US-only / Senior-Director noise.
     - Scores (0-100), deduplicates against seen/applied jobs, writes an Obsidian Vault
       dossier + CSV, and optionally delivers the ranked shortlist CSV directly to Telegram.

  2. Safe-Batch Direct-Email Staging & Paced Approval:
     - Extracts verified HR/careers emails (optionally scraping top candidate URLs via
       Firecrawl /v1/scrape).
     - Enforces a HARD daily Gmail safety cap (max 15/day, default 10) to protect
       omar.alnemr04@gmail.com deliverability.
     - Stages every email through job_application_engine.stage_job_application() ->
       approval_gatekeeper.py (strict Two-Phase Approval; NEVER transmits unapproved).
     - Supports paced batch dispatch (--approve-batch --pace-seconds 15) only when
       Omar explicitly authorizes it.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import approval_gatekeeper
import job_application_engine
import send_telegram_file

FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v1/search"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
ETIHAD_SR_API_URL = "https://api.smartrecruiters.com/v1/companies/EtihadAirways5/postings?limit=100"
REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs?limit=60"

HARD_MAX_DAILY_EMAILS = 15
DEFAULT_DAILY_EMAILS = 10

NO_ACCOUNT_ATS_DOMAINS: Dict[str, str] = {
    "jobs.lever.co": "Lever (No-Account)",
    "boards.greenhouse.io": "Greenhouse (No-Account)",
    "job-boards.greenhouse.io": "Greenhouse (No-Account)",
    "jobs.ashbyhq.com": "Ashby (No-Account)",
    "apply.workable.com": "Workable (No-Account)",
    "jobs.smartrecruiters.com": "SmartRecruiters (1-Click)",
    "breezy.hr": "BreezyHR (No-Account)",
    "recruitee.com": "Recruitee (No-Account)",
}

BLOCKED_EMAIL_DOMAINS = {
    "example.com", "example.org", "test.com", "domain.com", "email.com",
    "sentry.io", "wixpress.com", "cloudflare.com", "w3.org", "schema.org",
    "lever.co", "greenhouse.io", "ashbyhq.com", "workable.com",
    "smartrecruiters.com", "remotive.com", "linkedin.com", "facebook.com",
    "google.com", "googleapis.com", "github.com", "firecrawl.dev",
}

BLOCKED_EMAIL_PREFIXES = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "bounce", "mailer-daemon",
    "privacy", "legal", "dpo", "abuse", "security", "webmaster", "support@",
    "press@", "media@", "sales@", "marketing@", "billing@", "accessibility",
)

HR_EMAIL_PREFIX_HINTS = (
    "career", "careers", "hr", "jobs", "job", "recruit", "recruitment",
    "talent", "hiring", "apply", "cv", "resume", "people", "work", "join",
)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,10}\b")

EXCLUDE_TITLE_OR_DESC_PATTERNS = [
    r"\bemiratisation\b",
    r"\bemirati\s+national",
    r"\buae\s+national[s]?\b",
    r"\buaen\b",
    r"\bsaudi\s+national[s]?\s+only\b",
    r"\bus\s+citizen[s]?\s+only\b",
    r"\bus\s+(east|west)\s+coast\b",
    r"-\s*us\s*(@|$|\|)",
    r"\bhangzhou\b|\bchina\b",
    r"\bsecurity\s+clearance\s+required\b",
    r"\btop\s+secret\b",
    r"\bprincipal\b",
    r"\bstaff\s+engineer\b",
    r"\bvice\s+president\b",
    r"\bvp\s+of\b",
    r"\bchief\s+\w+\s+officer\b",
    r"\bdirector\b",
    r"\bhead\s+of\b",
    r"\bcaptain\b",
    r"\bfirst\s+officer\b",
    r"\btype\s+rated\b",
    r"\baircraft\s+maintenance\s+engineer\b",
    # Exclude roles specifically requiring languages Omar does not list
    r"\b(turkish|cantonese|mandarin|chinese|japanese|korean|german|french|spanish|portuguese|italian|dutch|polish|russian|vietnamese|thai|indonesian|malay|tagalog|hindi|urdu|swedish|norwegian|danish|finnish|greek|czech|romanian|hungarian|hebrew)\s*(speaker|speakers|speaking|fluent|native|language|support|customer|only|\)|$)",
    r"\bin\s+(turkish|cantonese|mandarin|chinese|japanese|korean|german|french|spanish|portuguese|italian|dutch|polish|russian|vietnamese)\b",
    # Exclude non-MENA/non-remote Asia-Pacific station-locked postings
    r"\b(shenzhen|shanghai|beijing|guangzhou|ho\s+chi\s+minh|hanoi|tokyo|osaka|seoul|manila|jakarta|bangkok|kuala\s+lumpur|mumbai|delhi|bangalore|chennai|hyderabad|karachi|lahore|islamabad)\b",
    # Exclude generic search/listicle titles
    r"^\d+\+?\s+.*\bjobs\b",
]
EXCLUDE_RE = re.compile("|".join(EXCLUDE_TITLE_OR_DESC_PATTERNS), re.IGNORECASE)

AGGREGATOR_OR_NON_DIRECT_HOSTS = (
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com", "reddit.com", "tiktok.com",
    "indeed.com", "glassdoor.com", "bayt.com", "gulftalent.com", "naukrigulf.com",
    "ziprecruiter.com", "jooble.org", "simplyhired.com", "monster.com", "talent.com",
    "careerjet.", "dubizzle.com", "olx.", "reed.co.uk", "totaljobs.com", "seek.com",
    "jobrapido.com", "adzuna.", "neuvoo.", "lensa.com", "builtIn.com",
)

# Search queries organized by target track
TRACK_QUERIES: Dict[str, List[Tuple[str, str]]] = {
    "aviation": [
        (
            "site:jobs.smartrecruiters.com (\"Etihad\" OR \"Emirates\" OR \"Air Arabia\" OR \"Qatar Airways\" OR \"flydubai\" OR \"dnata\" OR \"Swissport\" OR \"Menzies\") (\"passenger\" OR \"ground\" OR \"VIP\" OR \"customer\" OR \"check-in\" OR \"operations\")",
            "aviation",
        ),
        (
            "(\"Qatar Airways\" OR \"Air Arabia\" OR \"flydubai\" OR \"Gulf Air\" OR \"Riyadh Air\" OR \"Saudia\" OR \"Oman Air\" OR \"Royal Jordanian\") (\"customer service\" OR \"passenger service\" OR \"ground operations\" OR \"airport\" OR \"check-in\" OR \"reservations\") (\"apply\" OR \"careers\" OR \"jobs\")",
            "aviation",
        ),
        (
            "(site:jobs.lever.co OR site:boards.greenhouse.io OR site:apply.workable.com) (\"aviation\" OR \"airline\" OR \"travel\" OR \"Amadeus\" OR \"airport\") (\"operations\" OR \"support\" OR \"agent\" OR \"coordinator\" OR \"specialist\")",
            "aviation",
        ),
        (
            "(\"Dubai\" OR \"Abu Dhabi\" OR \"Doha\" OR \"Sharjah\" OR \"GCC\") (\"airport\" OR \"aviation\" OR \"airline\" OR \"travel consultant\" OR \"Amadeus\" OR \"ground operations\") (\"send your CV\" OR \"careers@\" OR \"hr@\" OR \"recruitment@\")",
            "aviation",
        ),
    ],
    "remote_support": [
        (
            "(site:jobs.lever.co OR site:boards.greenhouse.io OR site:jobs.ashbyhq.com) (\"Customer Support\" OR \"Technical Support\" OR \"Operations Specialist\") (\"Remote\" OR \"EMEA\" OR \"Arabic\" OR \"Worldwide\")",
            "remote_support",
        ),
        (
            "(site:apply.workable.com OR site:jobs.smartrecruiters.com) (\"Arabic\" OR \"Bilingual\" OR \"Remote\") (\"Customer Support\" OR \"Customer Service\" OR \"Trust & Safety\" OR \"Operations\")",
            "remote_support",
        ),
        (
            "(site:boards.greenhouse.io OR site:jobs.ashbyhq.com OR site:jobs.lever.co) (\"AI Trainer\" OR \"Data Evaluator\" OR \"Prompt\" OR \"QA Analyst\" OR \"Quality Assurance\") \"Remote\"",
            "remote_support",
        ),
        (
            "(\"Remote\" OR \"EMEA\" OR \"Arabic\") (\"Customer Support\" OR \"Technical Support\" OR \"Operations\") (\"send your CV\" OR \"apply via email\" OR \"careers@\" OR \"hr@\")",
            "remote_support",
        ),
    ],
    "tech_junior": [
        (
            "(site:jobs.lever.co OR site:boards.greenhouse.io OR site:jobs.ashbyhq.com) (\"IT Support\" OR \"Help Desk\" OR \"Service Desk\" OR \"Junior SOC\" OR \"Security Analyst\") (\"Remote\" OR \"EMEA\" OR \"Dubai\" OR \"Abu Dhabi\")",
            "tech_junior",
        ),
        (
            "(site:apply.workable.com OR site:jobs.smartrecruiters.com) (\"IT Support\" OR \"Junior QA\" OR \"Technical Support Engineer\" OR \"NOC\" OR \"Cybersecurity\") (\"Remote\" OR \"Dubai\" OR \"UAE\")",
            "tech_junior",
        ),
        (
            "(\"Remote\" OR \"Dubai\" OR \"Abu Dhabi\") (\"IT Support\" OR \"Helpdesk\" OR \"Junior Cybersecurity\" OR \"QA Tester\") (\"send your CV\" OR \"careers@\" OR \"hr@\" OR \"jobs@\")",
            "tech_junior",
        ),
    ],
}


def _load_env_key(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    candidates = [
        SCRIPT_DIR / ".env",
        Path.home() / ".hermes" / "profiles" / "llm-wiki" / ".env",
    ]
    for p in candidates:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and "=" in s:
                        k, v = s.split("=", 1)
                        if k.strip() == key:
                            return v.strip().strip("'\"")
            except Exception:
                pass
    return ""


def _get_state_dir() -> Path:
    base = Path(os.environ.get("AETHELGARD_HOME", os.path.expanduser("~/.aethelgard")))
    d = base / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_today_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _get_email_quota_state() -> Tuple[Path, Dict[str, Any], str]:
    qpath = _get_state_dir() / "job_hunter_email_quota.json"
    data = _load_json_file(qpath, {})
    today = _get_today_utc()
    if not isinstance(data, dict):
        data = {}
    day_entry = data.get(today) or {"staged": 0, "sent": 0, "recipients": []}
    data[today] = day_entry
    return qpath, data, today


def _canonicalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urllib.parse.urlsplit(u)
    clean_path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), clean_path, "", ""))


def _detect_ats(url: str) -> Tuple[str, bool]:
    """Returns (ats_label, is_no_account_portal)."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    for dom, label in NO_ACCOUNT_ATS_DOMAINS.items():
        if host == dom or host.endswith("." + dom):
            return label, True
    return host or "Web", False


def _is_valid_job_url(url: str) -> bool:
    """Filters out generic root domains, job aggregators, and search-result pages."""
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    if not host or not path:
        return False
    # Reject social feeds & aggregator search/list pages
    if any(b.lower() in host for b in AGGREGATOR_OR_NON_DIRECT_HOSTS):
        return False
    # Reject obvious search/query directory pages
    low_path = path.lower()
    if low_path.startswith("q-") or "/q-" in low_path or low_path.endswith("-jobs.html"):
        return False
    parts = [p for p in path.split("/") if p]
    # ATS portals must point to a specific job, not just company board root
    if host in ("jobs.lever.co", "jobs.ashbyhq.com", "jobs.smartrecruiters.com"):
        return len(parts) >= 2
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        return "jobs" in parts and len(parts) >= 3
    if host == "apply.workable.com":
        return "j" in parts and len(parts) >= 3
    return True


def _extract_hr_emails(text: str) -> List[str]:
    found = EMAIL_RE.findall(text or "")
    good: List[str] = []
    seen = set()
    for raw in found:
        em = raw.strip().strip(".,;:)>]").lower()
        if em in seen or "@" not in em:
            continue
        local, domain = em.split("@", 1)
        if domain in BLOCKED_EMAIL_DOMAINS or any(domain.endswith("." + b) for b in BLOCKED_EMAIL_DOMAINS):
            continue
        if any(local.startswith(p.rstrip("@")) or em.startswith(p) for p in BLOCKED_EMAIL_PREFIXES):
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|svg|webp|css|js)$", domain):
            continue
        seen.add(em)
        good.append(em)
    # Prioritize emails with HR/careers prefixes first
    good.sort(key=lambda e: (0 if any(h in e.split("@")[0] for h in HR_EMAIL_PREFIX_HINTS) else 1, e))
    return good


def _infer_company_from_url_and_title(url: str, title: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if host in ("jobs.lever.co", "boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.ashbyhq.com", "apply.workable.com", "jobs.smartrecruiters.com") and parts:
        raw_co = parts[0]
        raw_co = re.sub(r"\d+$", "", raw_co)
        raw_co = raw_co.replace("-", " ").replace("_", " ").strip()
        if raw_co.lower() == "etihadairways":
            return "Etihad Airways"
        if raw_co:
            return raw_co.title()
    # Try title patterns: "Role at Company", "Role - Company", "Company - Role"
    m = re.search(r"\bat\s+([A-Z][A-Za-z0-9 &.,'-]{1,40})$", title)
    if m:
        return m.group(1).strip(" -|")
    for sep in (" | ", " - ", " — "):
        if sep in title:
            chunks = [c.strip() for c in title.split(sep) if c.strip()]
            if len(chunks) >= 2:
                cand = chunks[-1]
                if not any(k in cand.lower() for k in ("lever", "greenhouse", "ashby", "workable", "smartrecruiters", "jobs", "careers")):
                    return cand[:45]
                if len(chunks) >= 3:
                    return chunks[-2][:45]
    return host.replace("www.", "").split(".")[0].title()


def _clean_role_title(title: str, company: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    for suffix in ("| SmartRecruiters", "- Lever", "| Greenhouse", "- Workable", "| Ashby"):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip(" -|")
    if company and t.lower().startswith(company.lower() + " "):
        t = t[len(company) :].strip(" -|:")
    return t[:110] or "Open Role"


def _score_job(job: Dict[str, Any]) -> Tuple[int, List[str]]:
    """Deterministic 0-100 profile fit score grounded in Omar Elnemr's verified background."""
    hay = f"{job.get('title', '')} {job.get('company', '')} {job.get('location', '')} {job.get('snippet', '')}".lower()
    score = 35
    reasons: List[str] = []

    if job.get("no_account_ats"):
        score += 15
        reasons.append("No-account ATS")
    if job.get("hr_email"):
        score += 18
        reasons.append("Direct HR email")

    # Location / remote eligibility
    if any(k in hay for k in ("remote", "worldwide", "work from home", "anywhere", "emea", "mena", "middle east")):
        score += 14
        reasons.append("Remote/EMEA")
    if any(k in hay for k in ("abu dhabi", "dubai", "uae", "qatar", "doha", "gcc", "saudi", "riyadh", "amman", "jordan", "damascus")):
        score += 12
        reasons.append("GCC/MENA hub")

    # Aviation & Etihad strengths
    if any(k in hay for k in ("amadeus", "altea", "altéa", "dcs", "check-in", "passenger service", "ground operations", "vip service", "airport", "airline", "aviation", "etihad")):
        score += 18
        reasons.append("Aviation/Amadeus match")

    # Arabic bilingual edge
    if "arabic" in hay or "bilingual" in hay:
        score += 14
        reasons.append("Arabic bilingual edge")

    # Remote support / operations / AI evaluation / junior tech fit
    if any(k in hay for k in ("customer support", "technical support", "customer service", "trust & safety", "operations specialist", "service desk", "help desk", "helpdesk", "it support")):
        score += 12
        reasons.append("Support/Ops match")
    if any(k in hay for k in ("ai trainer", "data evaluator", "prompt", "rlhf", "qa ", "quality assurance", "annotation")):
        score += 12
        reasons.append("AI Eval/QA match")
    if any(k in hay for k in ("junior", "entry level", "associate", "tier 1", "l1 ", "intern", "cybersecurity", "soc analyst")):
        score += 10
        reasons.append("Junior/IT match")

    # Penalties for mismatch / high seniority / strict region lock
    if any(k in hay for k in ("us only", "usa only", "united states only", "must reside in the us", "uk only", "canada only", "eu work permit required")):
        score -= 35
        reasons.append("Region restricted")
    if any(k in hay for k in ("senior", "sr.", "lead ", "manager", "5+ years", "7+ years", "8+ years", "10+ years")):
        score -= 18
        reasons.append("Higher seniority")

    return max(0, min(100, score)), reasons


def firecrawl_search(query: str, api_key: str, limit: int = 12) -> List[Dict[str, Any]]:
    payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")
    for attempt in range(2):
        req = urllib.request.Request(
            FIRECRAWL_SEARCH_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "AethelgardJobHunter/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            time.sleep(1.0)
            if not data.get("success"):
                return []
            raw_items = data.get("data") or []
            if isinstance(raw_items, dict):
                raw_items = raw_items.get("web") or []
            return raw_items if isinstance(raw_items, list) else []
        except urllib.error.HTTPError as he:
            if he.code == 429 and attempt == 0:
                time.sleep(6.0)
                continue
            print(f"[job_hunter] Firecrawl search warning ({query[:55]}...): HTTP {he.code}", file=sys.stderr)
            return []
        except Exception as exc:
            print(f"[job_hunter] Firecrawl search warning ({query[:55]}...): {exc}", file=sys.stderr)
            return []
    return []


def firecrawl_scrape_markdown(url: str, api_key: str) -> str:
    payload = json.dumps({"url": url, "formats": ["markdown"], "onlyMainContent": True}).encode("utf-8")
    req = urllib.request.Request(
        FIRECRAWL_SCRAPE_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AethelgardJobHunter/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if data.get("success"):
            return str(((data.get("data") or {}).get("markdown")) or "")
    except Exception as exc:
        print(f"[job_hunter] Firecrawl scrape warning ({url[:60]}): {exc}", file=sys.stderr)
    return ""


def fetch_etihad_smartrecruiters() -> List[Dict[str, Any]]:
    """Zero-credit public JSON feed for Etihad Airways SmartRecruiters postings."""
    results: List[Dict[str, Any]] = []
    try:
        req = urllib.request.Request(ETIHAD_SR_API_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        for item in data.get("content") or []:
            title = str(item.get("name") or "").strip()
            pid = str(item.get("id") or "").strip()
            loc_obj = item.get("location") or {}
            loc = ", ".join(p for p in [loc_obj.get("city"), loc_obj.get("country")] if p) or "Abu Dhabi, UAE"
            dept = str((item.get("department") or {}).get("label") or "")
            hay = f"{title} {dept} {loc}".lower()
            if not any(k in hay for k in ("guest", "vip", "customer", "service", "ground", "airport", "passenger", "operations", "agent", "coordinator", "support", "it ", "cyber", "security", "digital", "travel", "cabin", "lounge", "station", "ticketing", "check")):
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            url = f"https://jobs.smartrecruiters.com/EtihadAirways5/{pid}-{slug}"
            results.append({
                "title": title,
                "company": "Etihad Airways",
                "url": url,
                "location": loc,
                "snippet": f"Etihad Airways — {dept or 'Operations'} — {loc} (SmartRecruiters 1-click apply)",
                "track": "aviation",
                "source": "etihad_api",
                "hr_email": "",
            })
    except Exception as exc:
        print(f"[job_hunter] Etihad API warning: {exc}", file=sys.stderr)
    return results


def fetch_remotive_jobs() -> List[Dict[str, Any]]:
    """Zero-credit public JSON feed for fresh worldwide/EMEA remote roles."""
    results: List[Dict[str, Any]] = []
    try:
        req = urllib.request.Request(REMOTIVE_API_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        for item in data.get("jobs") or []:
            title = str(item.get("title") or "").strip()
            company = str(item.get("company_name") or "").strip()
            url = str(item.get("url") or "").strip()
            loc = str(item.get("candidate_required_location") or "Remote").strip()
            cat = str(item.get("category") or "").strip()
            desc_html = str(item.get("description") or "")
            desc_plain = re.sub(r"<[^>]+>", " ", desc_html)
            desc_plain = re.sub(r"\s+", " ", desc_plain).strip()
            hay = f"{title} {cat} {loc}".lower()
            if not any(k in hay for k in ("support", "customer", "operations", "qa", "quality", "security", "it ", "help", "ai", "data", "prompt", "travel", "service", "coordinator", "assistant", "analyst")):
                continue
            emails = _extract_hr_emails(desc_plain)
            track = "tech_junior" if any(k in hay for k in ("security", "qa", "it ", "help", "cyber", "developer")) else "remote_support"
            results.append({
                "title": title,
                "company": company,
                "url": url,
                "location": loc,
                "snippet": desc_plain[:380],
                "track": track,
                "source": "remotive_api",
                "hr_email": emails[0] if emails else "",
            })
    except Exception as exc:
        print(f"[job_hunter] Remotive API warning: {exc}", file=sys.stderr)
    return results


def _load_previously_tracked_urls(vault_dir: Optional[str] = None) -> set:
    seen_path = _get_state_dir() / "job_hunter_seen.json"
    seen_data = _load_json_file(seen_path, {"urls": []})
    seen = set(seen_data.get("urls") or [])
    tracker = job_application_engine.resolve_vault_dir(vault_dir) / "career" / "job_applications_tracker.md"
    if tracker.is_file():
        txt = tracker.read_text(encoding="utf-8", errors="ignore")
        for u in re.findall(r"https?://[^\s|)`]+", txt):
            seen.add(_canonicalize_url(u))
    return seen


def _save_seen_urls(urls: set) -> None:
    seen_path = _get_state_dir() / "job_hunter_seen.json"
    trimmed = sorted(urls)[-2000:]
    _save_json_file(seen_path, {"updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "urls": trimmed})


def hunt_jobs(
    tracks: List[str],
    limit: int = 50,
    per_query: int = 12,
    include_seen: bool = False,
    scrape_emails: int = 5,
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    api_key = _load_env_key("FIRECRAWL_API_KEY")
    raw_candidates: List[Dict[str, Any]] = []
    queries_run = 0
    scrapes_run = 0

    selected_tracks = list(TRACK_QUERIES.keys()) if "all" in tracks else [t for t in tracks if t in TRACK_QUERIES]
    if not selected_tracks:
        selected_tracks = list(TRACK_QUERIES.keys())

    # 1. Free zero-credit live feeds first
    if "aviation" in selected_tracks:
        raw_candidates.extend(fetch_etihad_smartrecruiters())
    if "remote_support" in selected_tracks or "tech_junior" in selected_tracks:
        raw_candidates.extend(fetch_remotive_jobs())

    # 2. Firecrawl web_search queries across no-account ATS & direct-email roles
    if api_key:
        for tr in selected_tracks:
            for q, track_name in TRACK_QUERIES[tr]:
                queries_run += 1
                hits = firecrawl_search(q, api_key=api_key, limit=per_query)
                for h in hits:
                    url = str(h.get("url") or "").strip()
                    title = str(h.get("title") or "").strip()
                    desc = str(h.get("description") or h.get("markdown") or "").strip()
                    if not url or not _is_valid_job_url(url):
                        continue
                    company = _infer_company_from_url_and_title(url, title)
                    role = _clean_role_title(title, company)
                    emails = _extract_hr_emails(f"{title} {desc}")
                    raw_candidates.append({
                        "title": role,
                        "company": company,
                        "url": url,
                        "location": "See posting",
                        "snippet": re.sub(r"\s+", " ", desc)[:380],
                        "track": track_name,
                        "source": "firecrawl_search",
                        "hr_email": emails[0] if emails else "",
                    })
    else:
        print("[job_hunter] Warning: FIRECRAWL_API_KEY not found; using public JSON feeds only.", file=sys.stderr)

    # 3. Deduplicate, filter & score
    prev_seen = set() if include_seen else _load_previously_tracked_urls(vault_dir=vault_dir)
    dedup_urls = set()
    dedup_keys = set()
    ranked: List[Dict[str, Any]] = []

    for cand in raw_candidates:
        canon = _canonicalize_url(cand.get("url", ""))
        if not canon or canon in dedup_urls or canon in prev_seen:
            continue
        hay_excl = f"{cand.get('title', '')} {cand.get('snippet', '')}"
        if EXCLUDE_RE.search(hay_excl):
            continue
        key = (cand.get("company", "").lower().strip(), re.sub(r"[^a-z0-9]+", "", cand.get("title", "").lower()))
        if key in dedup_keys:
            continue

        ats_label, is_no_account = _detect_ats(canon)
        cand["canonical_url"] = canon
        cand["ats"] = ats_label
        cand["no_account_ats"] = is_no_account
        score, reasons = _score_job(cand)
        if score < 40:
            continue
        cand["score"] = score
        cand["match_reasons"] = reasons
        dedup_urls.add(canon)
        dedup_keys.add(key)
        ranked.append(cand)

    ranked.sort(key=lambda x: (x["score"], 1 if x.get("hr_email") else 0, 1 if x.get("no_account_ats") else 0), reverse=True)

    # 4. Optional targeted Firecrawl scrape to uncover direct HR emails on top non-ATS postings
    if api_key and scrape_emails > 0:
        for job in ranked[: max(20, limit)]:
            if scrapes_run >= scrape_emails:
                break
            if job.get("hr_email") or job.get("no_account_ats"):
                continue
            md = firecrawl_scrape_markdown(job["url"], api_key=api_key)
            scrapes_run += 1
            if md:
                emails = _extract_hr_emails(md)
                if emails:
                    job["hr_email"] = emails[0]
                    job["score"], job["match_reasons"] = _score_job(job)

    ranked.sort(key=lambda x: (x["score"], 1 if x.get("hr_email") else 0, 1 if x.get("no_account_ats") else 0), reverse=True)
    shortlist = ranked[:limit]

    # Persist seen URLs
    all_seen = _load_previously_tracked_urls(vault_dir=vault_dir)
    for j in shortlist:
        all_seen.add(j["canonical_url"])
    _save_seen_urls(all_seen)

    return {
        "success": True,
        "tracks": selected_tracks,
        "firecrawl_queries_run": queries_run,
        "firecrawl_scrapes_run": scrapes_run,
        "raw_discovered": len(raw_candidates),
        "qualified_total": len(ranked),
        "shortlist_count": len(shortlist),
        "direct_email_count": sum(1 for j in shortlist if j.get("hr_email")),
        "no_account_ats_count": sum(1 for j in shortlist if j.get("no_account_ats")),
        "jobs": shortlist,
    }


def write_hunt_outputs(result: Dict[str, Any], vault_dir: Optional[str] = None) -> Dict[str, str]:
    vault_path = job_application_engine.resolve_vault_dir(vault_dir)
    career_dir = vault_path / "career"
    career_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    md_path = career_dir / f"job_hunt_{today}.md"
    csv_path = career_dir / f"job_hunt_{today}.csv"

    jobs = result.get("jobs") or []
    lines = [
        "---",
        f"title: Job Hunt Shortlist — {today}",
        "type: job-hunt",
        f"tracks: {json.dumps(result.get('tracks') or [])}",
        f"total_shortlisted: {len(jobs)}",
        "tags: [career, job-hunter, firecrawl, aethelgard]",
        "---",
        "",
        f"# Job Hunt Shortlist ({today})",
        "",
        f"- **Tracks:** {', '.join(result.get('tracks') or [])}",
        f"- **Shortlisted:** {len(jobs)} (from {result.get('raw_discovered', 0)} discovered)",
        f"- **No-Account ATS Portals:** {result.get('no_account_ats_count', 0)}",
        f"- **Direct HR Email Matches:** {result.get('direct_email_count', 0)}",
        "",
        "| # | Score | Track | Company | Role | ATS / Channel | HR Email | Direct Apply Link |",
        "| :--- | :---: | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for idx, j in enumerate(jobs, 1):
        co = j["company"].replace("|", "/")
        role = j["title"].replace("|", "/")
        ats = j["ats"].replace("|", "/")
        em = f"`{j['hr_email']}`" if j.get("hr_email") else "—"
        lines.append(f"| {idx} | **{j['score']}** | `{j['track']}` | {co} | {role} | {ats} | {em} | [Apply]({j['url']}) |")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as cf:
        writer = csv.writer(cf)
        writer.writerow(["Rank", "Score", "Track", "Company", "Role", "Location", "ATS", "NoAccount", "HREmail", "ApplyURL", "MatchReasons"])
        for idx, j in enumerate(jobs, 1):
            writer.writerow([
                idx,
                j["score"],
                j["track"],
                j["company"],
                j["title"],
                j.get("location", ""),
                j["ats"],
                "Yes" if j.get("no_account_ats") else "No",
                j.get("hr_email", ""),
                j["url"],
                "; ".join(j.get("match_reasons") or []),
            ])

    return {"markdown_path": str(md_path), "csv_path": str(csv_path)}


def stage_safe_email_batch(
    jobs: List[Dict[str, Any]],
    max_emails: int = DEFAULT_DAILY_EMAILS,
    notify_telegram: bool = False,
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Stages direct-email job applications strictly inside the daily Gmail safety cap
    (hard max 15/day) through approval_gatekeeper.py (pending_approval; zero auto-send).
    """
    qpath, qdata, today = _get_email_quota_state()
    day_entry = qdata[today]
    already_staged = int(day_entry.get("staged", 0))
    already_recipients = set(r.lower() for r in (day_entry.get("recipients") or []))

    capped_max = max(0, min(int(max_emails), HARD_MAX_DAILY_EMAILS))
    remaining_today = max(0, HARD_MAX_DAILY_EMAILS - already_staged)
    budget = min(capped_max, remaining_today)

    staged_drafts: List[Dict[str, Any]] = []
    skipped_reason = ""
    if budget <= 0:
        skipped_reason = f"Daily Gmail safety cap reached ({already_staged}/{HARD_MAX_DAILY_EMAILS} staged today)."
        return {
            "staged_count": 0,
            "daily_used": already_staged,
            "daily_cap": HARD_MAX_DAILY_EMAILS,
            "skipped_reason": skipped_reason,
            "drafts": [],
        }

    for j in jobs:
        if len(staged_drafts) >= budget:
            break
        em = (j.get("hr_email") or "").strip().lower()
        if not em or em in already_recipients:
            continue
        include_certs = j.get("track") == "aviation"
        res = job_application_engine.stage_job_application(
            company=j["company"],
            role=j["title"],
            to=em,
            channel="gmail",
            job_url=j["url"],
            job_description=j.get("snippet", ""),
            include_certificates=include_certs,
            tailor_cv=True,
            track_override=j.get("track", ""),
            notify_telegram=notify_telegram,
            vault_dir=vault_dir,
        )
        already_recipients.add(em)
        staged_drafts.append({
            "draft_id": res["draft_id"],
            "company": j["company"],
            "role": j["title"],
            "to": em,
            "subject": res["subject"],
            "attachments": res["attachment_names"],
        })

    day_entry["staged"] = already_staged + len(staged_drafts)
    day_entry["recipients"] = sorted(already_recipients)
    qdata[today] = day_entry
    _save_json_file(qpath, qdata)

    return {
        "staged_count": len(staged_drafts),
        "daily_used": day_entry["staged"],
        "daily_cap": HARD_MAX_DAILY_EMAILS,
        "drafts": staged_drafts,
    }


def list_pending_job_drafts() -> List[Dict[str, Any]]:
    pending = approval_gatekeeper.list_pending_drafts()
    out = []
    for d in pending:
        meta = d.get("metadata") or {}
        if d.get("platform") == "gmail" and (meta.get("role") or meta.get("company")):
            out.append({
                "draft_id": d["draft_id"],
                "company": meta.get("company", ""),
                "role": meta.get("role", ""),
                "to": d.get("recipient_or_target", ""),
                "subject": d.get("subject", ""),
                "attachments": [Path(str(a)).name for a in (meta.get("attachments") or [])],
            })
    return out


def approve_pending_batch(draft_ids: Optional[List[str]] = None, pace_seconds: int = 15) -> Dict[str, Any]:
    """
    Sends ONLY explicitly approved pending Gmail job drafts, paced safely and hard-capped
    at HARD_MAX_DAILY_EMAILS per UTC day.
    """
    qpath, qdata, today = _get_email_quota_state()
    day_entry = qdata[today]
    sent_today = int(day_entry.get("sent", 0))
    remaining = max(0, HARD_MAX_DAILY_EMAILS - sent_today)

    pending = list_pending_job_drafts()
    targets = [p for p in pending if not draft_ids or p["draft_id"] in draft_ids]
    targets = targets[:remaining]

    results = []
    for idx, item in enumerate(targets):
        res = approval_gatekeeper.approve_draft(item["draft_id"])
        results.append({"draft_id": item["draft_id"], "to": item["to"], "success": bool(res.get("success")), "detail": res.get("error") or "sent"})
        if res.get("success"):
            sent_today += 1
        if idx < len(targets) - 1 and pace_seconds > 0:
            time.sleep(pace_seconds)

    day_entry["sent"] = sent_today
    qdata[today] = day_entry
    _save_json_file(qpath, qdata)
    return {
        "dispatched": sum(1 for r in results if r["success"]),
        "attempted": len(results),
        "daily_sent": sent_today,
        "daily_cap": HARD_MAX_DAILY_EMAILS,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aethelgard Firecrawl Job Hunter & Safe-Batch Direct-Email Pipeline")
    parser.add_argument("--tracks", default="all", help="Comma-separated tracks: all, aviation, remote_support, tech_junior")
    parser.add_argument("--limit", type=int, default=40, help="Max ranked jobs in shortlist (default 40)")
    parser.add_argument("--per-query", type=int, default=12, help="Firecrawl results per query (default 12)")
    parser.add_argument("--scrape-emails", type=int, default=4, help="Max non-ATS pages to scrape via Firecrawl for HR emails (default 4)")
    parser.add_argument("--include-seen", action="store_true", help="Include previously hunted URLs")
    parser.add_argument("--stage-emails", action="store_true", help="Stage direct HR email drafts via Approval Gatekeeper (max 10-15/day)")
    parser.add_argument("--notify-approval", action="store_true", help="Send each tailored CV PDF + Telegram Approval Card for Omar's confirmation")
    parser.add_argument("--max-emails", type=int, default=DEFAULT_DAILY_EMAILS, help="Max direct email drafts to stage today (hard cap 15)")
    parser.add_argument("--send-telegram", action="store_true", help="Send the ranked CSV shortlist directly to Omar on Telegram")
    parser.add_argument("--list-pending", action="store_true", help="List pending job application email drafts awaiting approval")
    parser.add_argument("--approve-batch", nargs="*", help="Approve & send pending job drafts with safe pacing (optional list of draft_ids)")
    parser.add_argument("--pace-seconds", type=int, default=15, help="Delay between Gmail sends during --approve-batch (default 15s)")
    parser.add_argument("--json", action="store_true", help="Emit full JSON output")
    args = parser.parse_args()

    if args.list_pending:
        print(json.dumps({"pending_job_drafts": list_pending_job_drafts()}, indent=2, ensure_ascii=False))
        return 0

    if args.approve_batch is not None:
        ids = args.approve_batch if len(args.approve_batch) > 0 else None
        out = approve_pending_batch(draft_ids=ids, pace_seconds=args.pace_seconds)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    tracks = [t.strip().lower() for t in args.tracks.split(",") if t.strip()]
    hunt_res = hunt_jobs(
        tracks=tracks,
        limit=args.limit,
        per_query=args.per_query,
        include_seen=args.include_seen,
        scrape_emails=args.scrape_emails,
    )
    files = write_hunt_outputs(hunt_res)
    hunt_res["files"] = files

    if args.stage_emails:
        hunt_res["email_batch"] = stage_safe_email_batch(
            hunt_res["jobs"],
            max_emails=args.max_emails,
            notify_telegram=args.notify_approval,
        )

    if args.send_telegram and os.path.isfile(files["csv_path"]):
        caption = (
            f"🎯 Job Hunt Shortlist ({hunt_res['shortlist_count']} ranked roles | "
            f"{hunt_res['no_account_ats_count']} no-account ATS | "
            f"{hunt_res['direct_email_count']} direct HR emails)"
        )
        try:
            tg_res = send_telegram_file.send_file_to_telegram(files["csv_path"], caption=caption)
            hunt_res["telegram_csv_sent"] = bool(tg_res.get("ok"))
            hunt_res["telegram_message_id"] = (tg_res.get("result") or {}).get("message_id")
        except Exception as exc:
            hunt_res["telegram_csv_sent"] = False
            hunt_res["telegram_error"] = str(exc)

    if args.json:
        print(json.dumps(hunt_res, indent=2, ensure_ascii=False))
    else:
        summary = {k: v for k, v in hunt_res.items() if k != "jobs"}
        summary["top_15"] = [
            {
                "rank": i,
                "score": j["score"],
                "track": j["track"],
                "company": j["company"],
                "role": j["title"],
                "ats": j["ats"],
                "hr_email": j.get("hr_email", ""),
                "url": j["url"],
            }
            for i, j in enumerate(hunt_res["jobs"][:15], 1)
        ]
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
