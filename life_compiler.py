#!/usr/bin/env python3
"""
Aethelgard Bilingual Spoken "Voice-to-Life Compiler"
==================================================
Compiles messy bilingual (Arabic + English) voice transcripts or life updates
into structured updates across Omar's subsystems in one pass:
  1. Expenses      -> `expense_tracker.record_expense`
  2. Shifts        -> `shift_tracker.on_user_interaction`
  3. Promises/CRM  -> `promise_ledger.add_promise`
  4. Vault Memory  -> `$VAULT_DIR/log.md` structured delta note
"""

import datetime
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


PROXY_URL = os.environ.get("OMNIROUTE_URL", "http://127.0.0.1:20128/v1/chat/completions")
MODEL_NAME = "antigravity/gemini-3.8-flash-low"


def _heuristic_extract(text: str) -> Dict[str, Any]:
    """
    Deterministic bilingual Arabic/English extractor used offline or alongside AI extraction.
    """
    clean = (text or "").strip()
    expenses: List[str] = []
    promises: List[Dict[str, str]] = []
    shift_updates: List[str] = []
    notes: List[str] = []

    clauses = [c.strip() for c in re.split(r"(?<=[.!\?\u061F])\s+|\n+|(?:\s+(?:and also|وكمان|و أيضا|وأيضا|plus)\s+)", clean, flags=re.IGNORECASE) if c.strip()]

    expense_kw = re.compile(
        r"(\b\d+(?:\.\d+)?\s*(?:k|alf|syp|usd|\$|ليرة|ل\.س|ألف|الف|دولار)\b|spent|paid|bought|صرفت|صرفنا|دفعت|دفعنا|اشتريت)",
        re.IGNORECASE,
    )
    shift_kw = re.compile(
        r"(\bshift\b|\bشيف?ت\b|\bدوام\b|\bإجازة\b|\boff\b|\b(\d{1,2}:\d{2}\s*[-–→to]+\s*\d{1,2}:\d{2})\b)",
        re.IGNORECASE,
    )
    promise_kw = re.compile(
        r"(waiting on|follow\s*up|promised|remind me to|owe|sent cv|applied to|وعدت|لازم أبعت|لازم ابعت|متابعة|بانتظار|مستني)",
        re.IGNORECASE,
    )

    for clause in clauses:
        matched_any = False
        if expense_kw.search(clause):
            expenses.append(clause)
            matched_any = True
        if shift_kw.search(clause):
            shift_updates.append(clause)
            matched_any = True
        if promise_kw.search(clause):
            direction = "waiting_on_me" if re.search(r"(promised|remind me|i owe|وعدت|لازم أبعت|لازم ابعت)", clause, re.IGNORECASE) else "waiting_on_them"
            # Try to extract counterparty or organization
            cp_match = re.search(r"(?:to|with|from|on|لـ|مع|من|عند)\s+([A-Za-z\u0600-\u06FF0-9@._\-]{2,35})", clause, re.IGNORECASE)
            counterparty = cp_match.group(1) if cp_match else "FollowUp"
            promises.append({
                "counterparty": counterparty,
                "summary": clause[:140],
                "direction": direction,
            })
            matched_any = True
        if not matched_any and len(clause) >= 8:
            notes.append(clause)

    return {
        "expenses": expenses,
        "shift_updates": shift_updates,
        "promises": promises,
        "notes": notes,
        "extractor": "heuristic",
    }


def _ai_extract(text: str) -> Optional[Dict[str, Any]]:
    """Uses local Antigravity Proxy when reachable for nuanced bilingual structuring."""
    api_key = os.environ.get("OMNIROUTE_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "sk-omni")
    prompt = (
        "Extract structured personal life deltas from this bilingual Arabic/English transcript.\n"
        "Return strict JSON only with keys:\n"
        '- "expenses": list of short strings describing each expense (e.g. "50000 SYP fuel")\n'
        '- "shift_updates": list of strings if work shift times or off days are mentioned\n'
        '- "promises": list of {"counterparty": str, "summary": str, "direction": "waiting_on_them"|"waiting_on_me"}\n'
        '- "notes": list of general diary/concept facts worth logging in the Obsidian Vault\n\n'
        f"Transcript:\n{text}"
    )
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    try:
        req = urllib.request.Request(
            PROXY_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            parsed["extractor"] = "antigravity_ai"
            return parsed
    except Exception:
        return None
    return None


def compile_life_dump(
    transcript: str,
    vault_dir: Optional[str] = None,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """
    Compiles a spoken/text bilingual life update into concrete state updates across
    expenses, work shifts, promise ledger entries, and the Obsidian Vault log.
    """
    text = (transcript or "").strip()
    if not text:
        return {"compiled": False, "error": "Empty transcript"}

    target_vault = vault_dir or os.environ.get("VAULT_DIR") or os.path.expanduser("~/vault")
    extracted = _ai_extract(text) if use_ai else None
    if not extracted:
        extracted = _heuristic_extract(text)

    applied: Dict[str, Any] = {
        "compiled": True,
        "extractor": extracted.get("extractor", "heuristic"),
        "expenses_logged": [],
        "shift_updates_applied": [],
        "promises_added": [],
        "vault_log_appended": False,
    }

    # 1. Expenses
    for exp_str in extracted.get("expenses") or []:
        try:
            import expense_tracker
            res = expense_tracker.record_expense(str(exp_str), vault_dir=target_vault)
            applied["expenses_logged"].append(res)
        except Exception as exc:
            applied["expenses_logged"].append({"recorded": False, "error": str(exc)})

    # 2. Shifts
    for sh_str in extracted.get("shift_updates") or []:
        try:
            import shift_tracker
            msg = shift_tracker.on_user_interaction(target_vault, str(sh_str))
            applied["shift_updates_applied"].append(msg or f"Processed: {sh_str}")
        except Exception as exc:
            applied["shift_updates_applied"].append(f"Error: {exc}")

    # 3. Promises / Commitments
    for prm in extracted.get("promises") or []:
        if not isinstance(prm, dict):
            continue
        try:
            import promise_ledger
            entry = promise_ledger.add_promise(
                counterparty=str(prm.get("counterparty") or "FollowUp"),
                summary=str(prm.get("summary") or text[:100]),
                direction=str(prm.get("direction") or "waiting_on_them"),
                channel="voice_compiler",
                vault_dir=target_vault,
            )
            applied["promises_added"].append(entry)
        except Exception as exc:
            applied["promises_added"].append({"error": str(exc)})

    # 4. Append structured delta entry to Obsidian Vault log.md
    notes = extracted.get("notes") or []
    vault_path = Path(os.path.expanduser(target_vault))
    if vault_path.exists() and (notes or applied["expenses_logged"] or applied["promises_added"] or applied["shift_updates_applied"]):
        log_path = vault_path / "log.md"
        stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
        entry_lines = [
            f"\n## 🎙️ [{stamp}] Voice-to-Life Compile",
            f"> {text}",
        ]
        if applied["expenses_logged"]:
            entry_lines.append(f"- **Expenses logged:** {len(applied['expenses_logged'])}")
        if applied["shift_updates_applied"]:
            entry_lines.append(f"- **Shift updates:** {', '.join(str(x) for x in applied['shift_updates_applied'])}")
        if applied["promises_added"]:
            entry_lines.append(f"- **Promises tracked:** {len(applied['promises_added'])}")
        for n in notes:
            entry_lines.append(f"- **Note:** {n}")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(entry_lines) + "\n")
        applied["vault_log_appended"] = True

    return applied


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Bilingual Voice-to-Life Compiler")
    parser.add_argument("transcript", nargs="*", help="Spoken or typed bilingual life update")
    parser.add_argument("--vault", default=None, help="Override Obsidian Vault directory")
    parser.add_argument("--no-ai", action="store_true", help="Force deterministic offline extractor")
    args = parser.parse_args()

    text = " ".join(args.transcript).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()

    res = compile_life_dump(text, vault_dir=args.vault, use_ai=not args.no_ai)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("compiled") else 1


if __name__ == "__main__":
    sys.exit(main())
