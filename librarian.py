import os
import re
import datetime

class SecondBrainLibrarian:
    def __init__(self, vault_dir):
        self.vault_dir = os.path.abspath(os.path.expanduser(vault_dir))

    def scan_knowledge_notes(self):
        """Scans all non-system, non-archived Markdown notes in the vault."""
        notes = []
        for root, dirs, files in os.walk(self.vault_dir):
            rel_root = os.path.relpath(root, self.vault_dir)
            parts = rel_root.split(os.sep)
            if any(p.startswith(".") or p in ["_archive", "__pycache__", "scripts"] for p in parts):
                continue
            for f in files:
                if f.endswith(".md"):
                    notes.append(os.path.relpath(os.path.join(root, f), self.vault_dir))
        return sorted(notes)

    def audit_vault(self):
        """Audits index.md coverage, link targets, and orphans."""
        notes = self.scan_knowledge_notes()
        index_path = os.path.join(self.vault_dir, "index.md")
        
        index_content = ""
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                index_content = f.read()

        unindexed = []
        for n in notes:
            base = os.path.splitext(os.path.basename(n))[0]
            if base != "index" and f"[[{base}]]" not in index_content and f"[[{base}|" not in index_content:
                unindexed.append(n)

        # Audit links & incoming references
        note_links = {}
        incoming_links = {os.path.splitext(os.path.basename(n))[0]: 0 for n in notes}
        
        link_pattern = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")

        for n in notes:
            path = os.path.join(self.vault_dir, n)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                    targets = link_pattern.findall(text)
                    note_links[n] = targets
                    for t in targets:
                        clean_t = t.strip()
                        if clean_t in incoming_links:
                            incoming_links[clean_t] += 1
            except Exception:
                continue

        orphans = [n for n, count in incoming_links.items() if count == 0 and n not in ["index", "00 Profile", "Welcome"]]

        return {
            "total_notes": len(notes),
            "unindexed_count": len(unindexed),
            "unindexed_notes": unindexed,
            "orphan_count": len(orphans),
            "orphans": orphans[:15]
        }

    def sync_index_catalog(self):
        """Automatically appends unindexed notes to index.md in appropriate sections."""
        audit = self.audit_vault()
        unindexed = audit["unindexed_notes"]
        if not unindexed:
            return "✅ All knowledge notes are already cataloged in index.md."

        index_path = os.path.join(self.vault_dir, "index.md")
        if not os.path.exists(index_path):
            return "⚠ index.md not found."

        with open(index_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Categorize unindexed notes
        new_entries = []
        for n in unindexed:
            base = os.path.splitext(os.path.basename(n))[0]
            folder = os.path.dirname(n)
            clean_title = base.replace("-", " ").replace("_", " ").title()
            new_entries.append(f"- [[{base}|{clean_title}]] (`{folder or 'root'}`)\n")

        with open(index_path, "a", encoding="utf-8") as f:
            f.write("\n\n## 📚 Newly Ingested & Cataloged Notes (Automated Librarian)\n\n")
            f.writelines(new_entries)

        return f"✅ Librarian cataloged {len(new_entries)} notes into index.md."

    def generate_weekly_review(self):
        """Generates an executive weekly review summarizing team hours, vault growth, and metrics."""
        now = datetime.datetime.now()
        year, week_num, _ = now.isocalendar()
        review_title = f"{year}-W{week_num:02d} Executive Weekly Review"
        reviews_dir = os.path.join(self.vault_dir, "reviews")
        os.makedirs(reviews_dir, exist_ok=True)
        review_file = os.path.join(reviews_dir, f"{year}-W{week_num:02d}-executive-review.md")

        audit = self.audit_vault()
        
        # Read attendance file for roster metrics if available
        roster_path = os.path.join(self.vault_dir, "work_attendance_and_shift_roster.md")
        roster_summary = "Roster data maintained in [[work_attendance_and_shift_roster]]."
        leaderboard_section = ""
        if os.path.exists(roster_path):
            with open(roster_path, "r", encoding="utf-8") as rf:
                txt = rf.read()
                if "## 📊 1. Team Summary" in txt:
                    part = txt.split("## 📊 1. Team Summary")[1].split("---")[0]
                    leaderboard_section = "### 🏆 Current Team Hours Leaderboard\n" + part.strip()

        content = f"""---
title: {review_title}
date: {now.strftime('%Y-%m-%d')}
type: executive-review
tags: [review, operations, knowledge-management, team-metrics, aethelgard]
confidence: high
---

# 🏛️ {review_title}
Generated autonomously by **Aethelgard 24/7 Second Brain Librarian** on {now.strftime('%A, %B %d, %Y')}.

---

## 📊 1. Vault Health & Knowledge Metrics
- **Total Cataloged Knowledge Notes:** `{audit['total_notes']}`
- **Unindexed Notes Pending Catalog:** `{audit['unindexed_count']}`
- **Orphan / Standalone Notes:** `{audit['orphan_count']}`
- **Master Excel Timesheet:** `attendance_september_2026_standardized.xlsx`

---

## 🕒 2. Ground Operations & Shift Roster Status
{leaderboard_section or roster_summary}

---

## 🎯 3. Next Week Strategic Priorities
1. **Airport Shift Execution:** Maintain synchronized 07:20 AM bus log-ins and 16:00 return metrics.
2. **AI & Cybersecurity Intelligence:** Ingest next batch of red-teaming and OSINT playbooks into `concepts/`.
3. **Vault Cohesion:** Auto-link entity references across work and technical research.

---
*Autonomous synthesis by [[index|Aethelgard Vault]] Custodian.*
"""
        with open(review_file, "w", encoding="utf-8") as f:
            f.write(content)

        return review_file
