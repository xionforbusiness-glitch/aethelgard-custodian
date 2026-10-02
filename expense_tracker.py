import os
import re
import csv
import json
import datetime
import urllib.request

OMNIROUTE_PORT = os.environ.get("OMNIROUTE_PORT", "20128")
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", "sk-omniroute-redacted")
MODEL_NAME = os.environ.get("HERMES_MODEL", "antigravity/gemini-3.7-flash-high")

DEFAULT_SYP_PER_USD = 14800.0

def parse_expense_with_ai(text: str) -> dict:
    """Uses Gemini 3.7 Flash High to extract financial amounts, categories, and items from messy text/speech."""
    sys_prompt = (
        "You are an expert financial ledger accountant for Omar Elnemr living in Damascus, Syria. "
        "Extract financial transactions from natural language (English or Syrian Arabic dialect). "
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "amount_syp": <float or int, estimated total in SYP>,\n'
        '  "amount_usd": <float or int, equivalent in USD>,\n'
        '  "currency_stated": "<SYP or USD>",\n'
        '  "category": "<Food & Dining | Transport & Commute | Work & Airport | Groceries | Tech & Subscriptions | Personal & Lifestyle>",\n'
        '  "item": "<Short 3-5 word clean description of the expense>",\n'
        '  "notes": "<Any extra context like who he was with or where>"\n'
        "}\n"
        f"If the currency stated was USD, convert to SYP at {DEFAULT_SYP_PER_USD} SYP/USD. "
        f"If stated in SYP, convert to USD at 1 USD = {DEFAULT_SYP_PER_USD} SYP. "
        "Output ONLY the JSON object, no markdown ticks."
    )

    payload = json.dumps({
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": text}
        ],
        "temperature": 0.1
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{OMNIROUTE_PORT}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {OMNIROUTE_API_KEY}"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            raw_ans = res.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            raw_ans = re.sub(r"^```json\s*", "", raw_ans)
            raw_ans = re.sub(r"\s*```$", "", raw_ans)
            return json.loads(raw_ans)
    except Exception as e:
        # Offline / Fallback heuristic parser
        return parse_expense_fallback(text)

def parse_expense_fallback(text: str) -> dict:
    """Heuristic fallback parser when AI router is offline."""
    text_clean = text.replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:k|thousand|ألف)?", text_clean, re.I)
    amt = 0.0
    if match:
        raw_val = float(match.group(1))
        if ("k" in text.lower() or "ألف" in text) and raw_val < 1000:
            amt = raw_val * 1000.0
        else:
            amt = raw_val
            
    is_usd = "$" in text or "usd" in text.lower()
    amt_syp = amt * DEFAULT_SYP_PER_USD if is_usd else amt
    amt_usd = amt if is_usd else round(amt / DEFAULT_SYP_PER_USD, 2)
    
    cat = "Food & Dining"
    t_low = text.lower()
    if any(k in t_low for k in ["taxi", "bus", "transport", "fuel", "gas", "بنزين", "تكسي", "مواصلات"]):
        cat = "Transport & Commute"
    elif any(k in t_low for k in ["airport", "station", "uniform", "flight", "duty"]):
        cat = "Work & Airport"
    elif any(k in t_low for k in ["supermarket", "groceries", "market"]):
        cat = "Groceries"
        
    return {
        "amount_syp": amt_syp,
        "amount_usd": amt_usd,
        "currency_stated": "USD" if is_usd else "SYP",
        "category": cat,
        "item": text[:35],
        "notes": "Logged via instant parser"
    }

def record_expense(text: str, vault_dir: str) -> str:
    """Records the expense to both CSV and monthly Obsidian Markdown ledger, and returns a formatted report."""
    parsed = parse_expense_with_ai(text)
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    month_file = now.strftime("%Y-%m")

    finances_dir = os.path.join(vault_dir, "finances")
    os.makedirs(finances_dir, exist_ok=True)

    # 1. Update CSV
    csv_path = os.path.join(finances_dir, "expenses_2026.csv")
    csv_exists = os.path.exists(csv_path)
    with open(csv_path, "a", encoding="utf-8", newline="") as cf:
        writer = csv.writer(cf)
        if not csv_exists:
            writer.writerow(["Date", "Time", "Item", "Category", "Amount_SYP", "Amount_USD", "Notes"])
        writer.writerow([
            date_str,
            time_str,
            parsed.get("item", "Expense"),
            parsed.get("category", "General"),
            f"{parsed.get('amount_syp', 0):,.0f}",
            f"{parsed.get('amount_usd', 0):.2f}",
            parsed.get("notes", "")
        ])

    # 2. Update Monthly Markdown Ledger
    md_path = os.path.join(finances_dir, f"{month_file}-expenses.md")
    new_row = (
        f"| **{date_str}** | `{time_str}` | {parsed.get('item')} | "
        f"`{parsed.get('category')}` | **{parsed.get('amount_syp', 0):,.0f} SYP** | "
        f"`${parsed.get('amount_usd', 0):.2f}` | {parsed.get('notes', '—')} |\n"
    )

    if not os.path.exists(md_path):
        header = f"""---
title: Damascus Expense Ledger ({now.strftime('%B %Y')})
month: {month_file}
type: finance-ledger
tags: [finances, budget, expenses, damascus, syp, tracking]
created: {date_str}
---

# 💰 Damascus Expense & Cash Ledger ({now.strftime('%B %Y')})

Automated cash & spending ledger for Omar Elnemr. Converted at active benchmark rate `1 USD ≈ {DEFAULT_SYP_PER_USD:,.0f} SYP`.

---

## 📊 1. Monthly Financial Snapshot
- **Total Logged This Month:** Dynamic Ledger
- **Primary Budget Focus:** Awareness & curb impulse dining/rides

---

## 🧾 2. Detailed Transaction Log

| Date | Time | Item / Description | Category | Amount (SYP) | USD Equiv. | Notes |
| :--- | :---: | :--- | :--- | :---: | :---: | :--- |
"""
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(header + new_row)
    else:
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(new_row)

    # Calculate month total from CSV for quick reporting
    total_month_syp = 0
    total_month_usd = 0.0
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("Date", "").startswith(month_file):
                    try:
                        clean_syp = float(r.get("Amount_SYP", "0").replace(",", ""))
                        clean_usd = float(r.get("Amount_USD", "0"))
                        total_month_syp += clean_syp
                        total_month_usd += clean_usd
                    except Exception:
                        pass

    # Spending habit advice
    warning = ""
    if parsed.get("amount_syp", 0) > 100000:
        warning = "\n⚠️ *Notice:* Single transaction exceeded 100k SYP. Stay conscious of weekly allowance."

    response = (
        f"💸 *Expense Logged Successfully!*\n\n"
        f"• *Item:* {parsed.get('item')}\n"
        f"• *Category:* `{parsed.get('category')}`\n"
        f"• *Amount:* **{parsed.get('amount_syp', 0):,.0f} SYP** (~`${parsed.get('amount_usd', 0):.2f} USD`)\n"
        f"• *Month-to-Date:* **{total_month_syp:,.0f} SYP** (~`${total_month_usd:.2f} USD`)\n"
        f"• *Ledger:* `[[finances/{month_file}-expenses]]`"
        f"{warning}"
    )
    return response
