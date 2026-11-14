---
name: job-application
description: Prepare tailored job applications, cover emails, and recruiter pitches using Omar Elnemr's verified Aethelgard Vault work history, primary CVs, and Etihad Airways certificates, and stage outbound transmissions with MIME attachments via Approval Gatekeeper.
---

# Autonomous Job Application & Career Engine Skill

Whenever Omar asks you to apply for a job offer, draft a cover email, attach his CV/certificates, or reach out to a recruiter or airline/tech hiring team, **never refuse**. Execute `job_application_engine.py` (backed by `omnichannel_bridge.py` and `approval_gatekeeper.py`).

## Verified Assets Available Offline & on VM
- **CVs (`raw/documents/cv/` & `~/.aethelgard/career_assets/cv/`):**
  - Primary CV: `Omar_Elnemr_CV_2026.pdf` / `Omar_Elnemr_CV_2026.docx`
  - Alternate CVs: `Omar.pdf`, `Omar.docx`, `Omar_Version1_With_Bullets.pdf`, `Omar_Version2_Without_Bullets.pdf`
- **Etihad Airways L&D Certificates (`raw/documents/etihad_training/certificates/` & `~/.aethelgard/career_assets/certificates/`):**
  - 5 verified PDFs (IGS 16 Hrs, Amadeus Altéa CM 32 Hrs — 91%, INSPIRE 16 Hrs, Airport Disruption Management Workshop, AUG 2026-GSU-34)
- **Vault Career Notes:** `00 Profile.md`, `01 Technical Skills.md`, `03 Work Experience.md`, `professional_work_history.md`, `entities/etihad-airways-station-operations.md`

## Commands

### 1. Inspect Discovered CVs, Certificates & Profile
```bash
python job_application_engine.py --assets
python job_application_engine.py --profile
```

### 2. Stage a Tailored Job Application (Mandatory Two-Phase Approval)
```bash
# By email with auto-attached CV (+ Etihad certificates when relevant)
python job_application_engine.py --apply --company "Etihad Airways" --role "Passenger Service Agent" --to "careers@example.com" --channel gmail --notify

# From a job posting URL
python job_application_engine.py --apply --company "Company Name" --role "Role Title" --url "https://example.com/jobs/123" --to "hr@example.com" --channel gmail --notify
```

### 3. Direct Email with Custom Attachments (`omnichannel_bridge.py`)
```bash
python omnichannel_bridge.py google --stage-email --to "hr@example.com" --subject "Application - Omar Elnemr" --body "..." --attach ~/.aethelgard/career_assets/cv/Omar_Elnemr_CV_2026.pdf
```

### 4. Phase 2 — Present & Wait for Explicit Confirmation
Present the `draft_id`, recipient, subject, attached filenames, and exact body to Omar on Telegram. Only after Omar replies `"yes"`, `"send"`, or `"approve <draft_id>"`, execute:
```bash
python approval_gatekeeper.py --approve <draft_id>
```
