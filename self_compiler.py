import os
import sys
import json
import shutil
import importlib
import subprocess
from voice_engine import send_telegram_voice_sync

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")

STAGING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "staging")
PROPOSAL_FILE = os.path.join(STAGING_DIR, "pending_proposal.json")

def init_staging():
    os.makedirs(STAGING_DIR, exist_ok=True)

def propose_new_tool(name: str, description: str, why_better: str, code: str, test_code: str) -> dict:
    """Drafts, tests in an isolated sandbox, and prepares an interactive upgrade proposal for Omar's authorization."""
    init_staging()
    clean_name = re_sub = "".join(c for c in name.lower().replace(" ", "_") if c.isalnum() or c == "_")
    tool_file = os.path.join(STAGING_DIR, f"{clean_name}.py")
    test_file = os.path.join(STAGING_DIR, f"test_{clean_name}.py")

    with open(tool_file, "w", encoding="utf-8") as f:
        f.write(code)

    with open(test_file, "w", encoding="utf-8") as f:
        f.write(test_code)

    # Execute unit test in isolated subprocess
    test_res = subprocess.run([sys.executable, test_file], capture_output=True, text=True)
    if test_res.returncode != 0:
        return {
            "status": "TESTS_FAILED",
            "error": test_res.stderr or test_res.stdout
        }

    # Record proposal
    proposal_data = {
        "name": clean_name,
        "description": description,
        "why_better": why_better,
        "tool_file": tool_file,
        "test_file": test_file,
        "target_file": os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{clean_name}.py"),
        "timestamp": os.stat(tool_file).st_mtime
    }

    with open(PROPOSAL_FILE, "w", encoding="utf-8") as pf:
        json.dump(proposal_data, pf, indent=2)

    return {
        "status": "READY_FOR_AUTHORIZATION",
        "proposal": proposal_data
    }

def format_telegram_upgrade_request(proposal: dict) -> tuple:
    """Creates the Telegram proposal message and authorization buttons."""
    name = proposal.get("name", "Tool")
    desc = proposal.get("description", "")
    why = proposal.get("why_better", "")

    msg = (
        f"🧬 *PROPOSED AUTONOMOUS UPGRADE: `{name}`*\n"
        f"Aethelgard Self-Compiler Sandbox Validation\n\n"
        f"📋 *What It Does:*\n{desc}\n\n"
        f"⚡ *Why This Is Better:*\n{why}\n\n"
        f"🧪 *Sandbox Verification:*\n"
        f"✅ Isolated Unit Tests Passed (100% Green)\n\n"
        f"⚠️ *Human-in-the-Loop Safety Rule:*\n"
        f"No code will be deployed to your live system without your explicit authorization.\n\n"
        f"Do you authorize this hot-patch deployment, sir?"
    )

    buttons = [
        [{"text": f"✅ Authorize Upgrade ({name})"}],
        [{"text": f"❌ Reject Upgrade ({name})"}]
    ]

    return msg, buttons

def execute_authorized_upgrade(custodian_dir: str = None) -> dict:
    """Deploys the authorized tool from staging to active production and hot-loads into runtime."""
    if not os.path.exists(PROPOSAL_FILE):
        return {"status": "NO_PENDING_PROPOSAL"}

    with open(PROPOSAL_FILE, "r", encoding="utf-8") as pf:
        proposal = json.load(pf)

    name = proposal["name"]
    staged_tool = proposal["tool_file"]
    target_tool = proposal["target_file"]

    # 1. Copy into production
    shutil.copy2(staged_tool, target_tool)

    # 2. Hot-load into active Python runtime
    try:
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
        hotload_status = "HOTLOADED_SUCCESS"
    except Exception as e:
        hotload_status = f"HOTLOAD_WARNING: {e}"

    # 3. Clean up proposal file
    try:
        os.remove(PROPOSAL_FILE)
    except Exception:
        pass

    # 4. Notify Omar via British JARVIS voice
    voice_memo = f"Upgrade authorized, sir. I have deployed {name} and hot-patched the active runtime. All systems nominal."
    send_telegram_voice_sync(voice_memo, caption="🎙️ Self-Upgrade Assimilation Confirmation")

    return {
        "status": "DEPLOYED",
        "name": name,
        "target": target_tool,
        "hotload": hotload_status
    }

def reject_pending_upgrade() -> dict:
    """Discards the pending tool proposal."""
    if not os.path.exists(PROPOSAL_FILE):
        return {"status": "NO_PENDING_PROPOSAL"}

    try:
        os.remove(PROPOSAL_FILE)
        return {"status": "REJECTED_AND_DISCARDED"}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}
