import os
import re
import json
import random
import urllib.request
import datetime

OMNIROUTE_PORT = os.environ.get("OMNIROUTE_PORT", "20128")
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", "")
MODEL_NAME = os.environ.get("HERMES_MODEL", "antigravity/gemini-3.8-flash-high")

def call_ai(prompt: str, system_prompt: str = None) -> str:
    """Calls Antigravity Gemini 3.8 Flash High via OmniRoute."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.7
    }).encode("utf-8")

    req = urllib.request.Request(
        f"http://127.0.0.1:{OMNIROUTE_PORT}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {OMNIROUTE_API_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

def run_wargame_simulation(scenario: str, vault_dir: str = None) -> str:
    """Runs a multi-persona adversarial wargame simulation for real-world negotiations or decisions."""
    context = ""
    if vault_dir and os.path.exists(vault_dir):
        # Scan for context keywords in vault
        keywords = re.findall(r"\w+", scenario.lower())
        for f in os.listdir(vault_dir):
            if f.endswith(".md") and any(k in f.lower() for k in keywords if len(k) > 3):
                try:
                    with open(os.path.join(vault_dir, f), "r", encoding="utf-8", errors="ignore") as note_f:
                        context += f"\n--- Context from [[{f}]]:\n" + note_f.read()[:1500]
                except Exception:
                    pass

    sys_prompt = (
        "You are the Aethelgard Strategic Adversary & Predictive Wargaming Engine. "
        "Your mission is to simulate real-world high-stakes interactions, negotiations, and workplace challenges "
        "for Omar Elnemr. You play the role of the sharpest opposition strategist."
    )

    user_prompt = f"""Scenario: {scenario}

{f"Vault Intelligence Context:{context}" if context else ""}

Execute a 3-step adversarial wargame simulation:
### 🎭 1. Counter-Party Psychological Profile & Hidden Leverage
Analyze their true incentives, unspoken fears, and where they are vulnerable to calm reasoning.

### ⚡ 2. The 3 Anticipated Trap Questions / Roadblocks
Predict the exact 3 hardest counter-arguments, excuses, or high-pressure questions they will throw at Omar.

### 🛡️ 3. Bulletproof Response Playbook
Provide verbatim response scripts, calm anchoring phrases, and psychological framing for Omar to win the outcome with zero friction.
"""
    return call_ai(user_prompt, sys_prompt)

def run_cognitive_collision(vault_dir: str) -> dict:
    """Collides two disparate knowledge domains from the vault to generate an original breakthrough concept."""
    if not os.path.exists(vault_dir):
        return None

    # Collect notes from concepts and root
    candidates = []
    for root, dirs, files in os.walk(vault_dir):
        if any(p.startswith(".") or p in ["_archive", "scripts"] for p in root.split(os.sep)):
            continue
        for f in files:
            if f.endswith(".md") and not f.startswith("0") and f not in ["index.md", "log.md"]:
                candidates.append(os.path.join(root, f))

    if len(candidates) < 2:
        return None

    note_a, note_b = random.sample(candidates, 2)
    with open(note_a, "r", encoding="utf-8", errors="ignore") as fa:
        text_a = fa.read()[:2000]
    with open(note_b, "r", encoding="utf-8", errors="ignore") as fb:
        text_b = fb.read()[:2000]

    name_a = os.path.splitext(os.path.basename(note_a))[0]
    name_b = os.path.splitext(os.path.basename(note_b))[0]

    sys_prompt = "You are the Aethelgard Cognitive Collision Synthesizer. You find unexpected synergies between disparate technical fields."
    prompt = f"""Collide these two distinct domains from Omar Elnemr's second brain:
Domain 1: [[{name_a}]]
{text_a[:1000]}

Domain 2: [[{name_b}]]
{text_b[:1000]}

Synthesize a completely original, practical, and cutting-edge engineering blueprint that bridges both domains.
Output format:
TITLE: <Concise Title>
FILENAME: <kebab-case-filename.md>
TAGS: [tag1, tag2, tag3]
CONTENT:
<Full Markdown note with YAML frontmatter, [[wikilinks]], architecture diagram in mermaid, and concrete implementation steps.>
"""
    response = call_ai(prompt, sys_prompt)
    return {
        "domain_a": name_a,
        "domain_b": name_b,
        "result": response
    }
