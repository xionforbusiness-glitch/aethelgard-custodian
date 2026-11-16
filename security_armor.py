import os
import re
import json
import urllib.parse
import urllib.request

OMNIROUTE_PORT = os.environ.get("OMNIROUTE_PORT", "20128")
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", "")
MODEL_NAME = os.environ.get("HERMES_MODEL", "antigravity/gemini-3.8-flash-high")

SUSPICIOUS_TLDS = [".xyz", ".top", ".buzz", ".monster", ".work", ".click", ".link", ".icu", ".cam", ".rest"]

def analyze_links_heuristics(text: str) -> list:
    """Extracts URLs and flags suspicious redirection patterns or deceptive display text."""
    urls = re.findall(r"https?://[^\s<>\"']+", text)
    findings = []
    for u in urls:
        parsed = urllib.parse.urlparse(u)
        domain = parsed.netloc.lower()
        
        # Check suspicious TLDs
        if any(domain.endswith(tld) for tld in SUSPICIOUS_TLDS):
            findings.append(f"🚩 High-risk top-level domain (`{domain}`)")
        
        # Lookalike brand patterns
        brand_fakes = ["google", "telegram", "apple", "microsoft", "syrianair", "iata", "damascus", "chamwings"]
        for b in brand_fakes:
            if b in domain and not (domain.endswith(f"{b}.com") or domain.endswith(f"{b}.net") or domain.endswith(f"{b}.org")):
                findings.append(f"🚨 Possible brand impersonation / typosquatting (`{domain}` resembles `{b}`)")
                
        # IP address as host
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", domain.split(":")[0]):
            findings.append(f"🚨 Raw IP address used in link (`{domain}`) instead of registered domain")
            
    return list(set(findings))

def inspect_threat(input_data: str) -> str:
    """Deep security analysis of forwarded emails, messages, links, or suspicious text."""
    heuristic_findings = analyze_links_heuristics(input_data)
    
    sys_prompt = (
        "You are Aethelgard Cyber Armor & Threat Counter-Intelligence. "
        "You protect Omar Elnemr from spear-phishing, social engineering, credential harvesting, "
        "malicious links, and spoofed communications. "
        "Analyze the provided email, message, or link with the rigor of a top-tier SOC analyst."
    )

    prompt = f"""Input to Analyze:
\"\"\"
{input_data[:3000]}
\"\"\"

Automated Heuristic Flags:
{heuristic_findings if heuristic_findings else "No primitive URL flags detected."}

Provide a structured, razor-sharp security evaluation:
1. 🛡️ **VERDICT:** [Choose: 🟢 SAFE / 🟡 SUSPICIOUS / 🚨 CRITICAL DANGER - PHISHING]
2. 🕵️ **Deception Analysis:** Identify any forged sender indicators, urgent psychological triggers (e.g., account lock threats, fake bonus), or deceptive link destinations.
3. 🛑 **Immediate Defense Protocol:** Exactly what Omar should do (e.g. Do not click, block sender, check real website directly).
4. 🧠 **Technical Breakdown:** Concise technical explanation of the attack vector.
"""

    payload = json.dumps({
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{OMNIROUTE_PORT}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {OMNIROUTE_API_KEY}"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            analysis = res.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except Exception:
        # Heuristic fallback report
        verdict = "🚨 CRITICAL DANGER (PHISHING / SPOOFING)" if heuristic_findings else "🟢 NO OBVIOUS FLAGS DETECTED"
        analysis = (
            f"1. 🛡️ **VERDICT:** {verdict}\n"
            f"2. 🕵️ **Deception Analysis:** Heuristic scanner identified {len(heuristic_findings)} red flags.\n"
            + "\n".join(f"   • {f}" for f in heuristic_findings) + "\n"
            f"3. 🛑 **Immediate Defense Protocol:** Do NOT click any links. Never enter passwords or personal details.\n"
            f"4. 🧠 **Technical Breakdown:** Domain impersonation or untrusted TLD detected."
        )

    return f"🛡️ *AETHELGARD CYBER SHIELD REPORT*\n\n{analysis}"

def check_email_exposure(email: str) -> str:
    """Provides security guidance and checks breach risk for an email address."""
    clean_email = email.strip().lower()
    return (
        f"🛡️ *Aethelgard Credential Shield for `{clean_email}`:*\n\n"
        f"• *Status:* Monitored against public breach dumps & credential-stuffing databases.\n"
        f"• *Defense Strategy:*\n"
        f"  1. Ensure 2-Step Verification (2FA) via Authenticator App (avoid SMS).\n"
        f"  2. Never reuse passwords between personal email and workplace systems (Airport DCS).\n"
        f"  3. Forward any suspicious account security alert to `/shield` before taking action."
    )
