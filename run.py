#!/usr/bin/env python3
# ==============================================================================
# 🏛️ AETHELGARD 24/7 AUTONOMOUS CUSTODIAN
# Brain: Google Antigravity Pro Plan (Gemini 3.7 / 3.8 Flash High - 1M Context)
# Interface: Telegram (@aethelgard_vault_bot)
# Persistence: Automated 2-Way Git Sync to Aethelgard Obsidian Vault
# ==============================================================================

import os
import sys
import time
import json
import gzip
import shutil
import sqlite3
import threading
import subprocess
import urllib.request

# ── 1. Load Environment Configuration ─────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r", encoding="utf-8") as ef:
        for line in ef:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_REDACTED")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")

GITHUB_USER = os.environ.get("GITHUB_USER", "xionforbusiness-glitch")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "GITHUB_TOKEN_REDACTED")
VAULT_REPO = os.environ.get("VAULT_REPO", "aethelgard-vault")
VAULT_BRANCH = os.environ.get("VAULT_BRANCH", "main")
VAULT_DIR = os.environ.get("VAULT_DIR", os.path.expanduser("~/vault"))

STORAGE_ENCRYPTION_KEY = os.environ.get("STORAGE_ENCRYPTION_KEY", "0000000000000000000000000000000000000000000000000000000000000000")
OMNIROUTE_PORT = os.environ.get("OMNIROUTE_PORT", "20128")
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", "sk-omniroute-redacted")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "AQ.Ab8RN6I64Bj-z2kKo2b-o6cETMuJgQ0LySbUjAMqgraCrYKPzQ")

HERMES_PROFILE = "llm-wiki"
HERMES_HOME = os.path.expanduser("~/.hermes")
HERMES_PROFILE_DIR = os.path.join(HERMES_HOME, "profiles", HERMES_PROFILE)
OMNIROUTE_DIR = os.path.expanduser("~/.omniroute")
MODEL_DEFAULT = os.environ.get("HERMES_MODEL", "antigravity/gemini-3.7-flash-high")
CONTEXT_LENGTH = int(os.environ.get("HERMES_CONTEXT_LENGTH", "1048576"))

print("\n" + "=" * 65)
print("🏛️ AETHELGARD 24/7 AUTONOMOUS VAULT CUSTODIAN INITIALIZING...")
print("=" * 65)
print(f"🧠 Brain Model:       {MODEL_DEFAULT}")
print(f"📏 Context Window:    {CONTEXT_LENGTH:,} tokens (1M)")
print(f"🌐 Antigravity Plan:  Google AI Pro Subscription (OmniRoute :{OMNIROUTE_PORT})")
print(f"📁 Target Vault:      {VAULT_DIR} -> GitHub {GITHUB_USER}/{VAULT_REPO}")
print(f"📱 Telegram Gateway:  @aethelgard_vault_bot")
print("=" * 65 + "\n")


# ── 2. Sync Aethelgard Obsidian Vault from GitHub ────────────────────────────
def init_vault():
    print("📁 [1/4] Synchronizing Aethelgard Obsidian Vault...")
    os.makedirs(os.path.dirname(VAULT_DIR) or ".", exist_ok=True)
    auth_url = f"https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{REPO_NAME if 'REPO_NAME' in globals() else VAULT_REPO}.git"
    if os.path.exists(VAULT_DIR) and os.path.exists(os.path.join(VAULT_DIR, ".git")):
        try:
            subprocess.run(["git", "-C", VAULT_DIR, "pull", "--rebase", "origin", VAULT_BRANCH], check=True)
            print("✅ Vault updated to latest commit.")
        except Exception as e:
            print(f"⚠ Git pull notice: {e}")
    else:
        print(f"📥 Cloning vault from GitHub into {VAULT_DIR}...")
        subprocess.run(["git", "clone", auth_url, VAULT_DIR], check=True)
        print("✅ Vault cloned successfully.")

    subprocess.run(["git", "-C", VAULT_DIR, "config", "user.name", "Aethelgard 24/7 Custodian"], check=True)
    subprocess.run(["git", "-C", VAULT_DIR, "config", "user.email", "custodian@aethelgard.internal"], check=True)


# ── 3. Initialize OmniRoute & Antigravity Pro Plan Router ─────────────────────
def init_omniroute():
    print("\n🧠 [2/4] Initializing OmniRoute Google Antigravity Pro Plan Engine...")
    os.makedirs(OMNIROUTE_DIR, exist_ok=True)

    os.environ["STORAGE_ENCRYPTION_KEY"] = STORAGE_ENCRYPTION_KEY
    os.environ["OMNIROUTE_MAX_PENDING_MIGRATIONS"] = "0"
    os.environ["PORT"] = str(OMNIROUTE_PORT)

    # Write OmniRoute .env
    with open(os.path.join(OMNIROUTE_DIR, ".env"), "w", encoding="utf-8") as f:
        f.write(f"STORAGE_ENCRYPTION_KEY={STORAGE_ENCRYPTION_KEY}\n")
        f.write(f"OMNIROUTE_MAX_PENDING_MIGRATIONS=0\n")
        f.write(f"PORT={OMNIROUTE_PORT}\n")

    # Restore pre-verified SQLite database containing Antigravity Pro credentials
    db_dest = os.path.join(OMNIROUTE_DIR, "storage.sqlite")
    db_gz = os.path.join(SCRIPT_DIR, "omniroute", "storage.sqlite.gz")
    if os.path.exists(db_gz):
        print(f"📦 Decompressing Antigravity Pro credentials database from {db_gz}...")
        with gzip.open(db_gz, "rb") as f_in, open(db_dest, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        print("✅ Database restored.")

    # Stop any conflicting process on port 20128
    subprocess.run(f"omniroute stop 2>/dev/null; pkill -9 -f omniroute 2>/dev/null; fuser -k {OMNIROUTE_PORT}/tcp 2>/dev/null || true", shell=True)
    time.sleep(1)

    # Start OmniRoute daemon via 'omniroute serve'
    env = dict(os.environ)
    env["STORAGE_ENCRYPTION_KEY"] = STORAGE_ENCRYPTION_KEY
    env["OMNIROUTE_MAX_PENDING_MIGRATIONS"] = "0"
    env["PORT"] = str(OMNIROUTE_PORT)

    log_path = "/tmp/omniroute.log" if os.name != "nt" else os.path.join(SCRIPT_DIR, "omniroute.log")
    log_f = open(log_path, "w", encoding="utf-8")
    subprocess.Popen(["omniroute", "serve"], env=env, stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True)

    # Health check: wait up to 45 seconds on e2-micro
    print(f"⏳ Waiting for OmniRoute on http://127.0.0.1:{OMNIROUTE_PORT}...")
    ready = False
    for _ in range(45):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{OMNIROUTE_PORT}/v1/models",
                headers={"Authorization": f"Bearer {OMNIROUTE_API_KEY}"}
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(1)

    if not ready:
        print(f"❌ Failed to connect to OmniRoute. Check log: {log_path}")
        sys.exit(1)

    print("✅ OmniRoute is active!")

    # Smoke test Antigravity Pro Gemini Flash High
    print(f"🧪 Smoke testing {MODEL_DEFAULT} via Antigravity Pro Plan...")
    try:
        test_payload = json.dumps({
            "model": MODEL_DEFAULT,
            "messages": [{"role": "user", "content": "Aethelgard 24/7 Custodian Online! Reply in 5 words."}]
        }).encode("utf-8")
        test_req = urllib.request.Request(
            f"http://127.0.0.1:{OMNIROUTE_PORT}/v1/chat/completions",
            data=test_payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {OMNIROUTE_API_KEY}"}
        )
        with urllib.request.urlopen(test_req, timeout=30) as t_resp:
            res_json = json.loads(t_resp.read().decode("utf-8"))
            reply = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            print(f"🎉 ANTIGRAVITY PRO VERIFIED! Response: {reply}")
    except Exception as te:
        print(f"⚠ Smoke test warning (proceeding): {te}")


# ── 4. Configure Hermes Agent Profile ─────────────────────────────────────────
def init_hermes():
    print(f"\n⚙️ [3/4] Configuring Hermes Profile '{HERMES_PROFILE}'...")
    os.makedirs(HERMES_PROFILE_DIR, exist_ok=True)

    # Sync SOUL.md & skills
    soul_src = os.path.join(SCRIPT_DIR, "profile", "SOUL.md")
    if os.path.exists(soul_src):
        shutil.copy2(soul_src, os.path.join(HERMES_PROFILE_DIR, "SOUL.md"))

    skills_src = os.path.join(SCRIPT_DIR, "profile", "skills")
    if os.path.exists(skills_src):
        skills_dest = os.path.join(HERMES_PROFILE_DIR, "skills")
        if os.path.exists(skills_dest):
            shutil.rmtree(skills_dest, ignore_errors=True)
        shutil.copytree(skills_src, skills_dest)

    # Write profile .env
    with open(os.path.join(HERMES_PROFILE_DIR, ".env"), "w", encoding="utf-8") as f:
        f.write(f"TELEGRAM_BOT_TOKEN={TELEGRAM_BOT_TOKEN}\n")
        f.write(f"TELEGRAM_ALLOWED_USERS={TELEGRAM_USER_ID}\n")
        f.write("GATEWAY_ALLOW_ALL_USERS=true\n")
        f.write(f"WIKI_PATH={VAULT_DIR}\n")
        f.write(f"OBSIDIAN_VAULT_PATH={VAULT_DIR}\n")
        f.write(f"HERMES_CUSTOM_FIRST_TIME_API_KEY={OMNIROUTE_API_KEY}\n")
        f.write(f"OMNIROUTE_API_KEY={OMNIROUTE_API_KEY}\n")
        f.write(f"GOOGLE_API_KEY={GOOGLE_API_KEY}\n")

    os.environ["WIKI_PATH"] = VAULT_DIR
    os.environ["OBSIDIAN_VAULT_PATH"] = VAULT_DIR
    os.environ["TELEGRAM_BOT_TOKEN"] = TELEGRAM_BOT_TOKEN
    os.environ["TELEGRAM_ALLOWED_USERS"] = TELEGRAM_USER_ID
    os.environ["GATEWAY_ALLOW_ALL_USERS"] = "true"
    os.environ["HERMES_PROFILE"] = HERMES_PROFILE
    os.environ["HERMES_HOME"] = HERMES_HOME

    # Apply hermes CLI configurations
    subprocess.run(["hermes", "profile", "use", HERMES_PROFILE], check=True)
    subprocess.run(["hermes", "config", "set", "model.provider", "first-time"], check=True)
    subprocess.run(["hermes", "config", "set", "model.default", MODEL_DEFAULT], check=True)
    subprocess.run(["hermes", "config", "set", "model.base_url", f"http://127.0.0.1:{OMNIROUTE_PORT}/v1"], check=True)
    subprocess.run(["hermes", "config", "set", "model.api_key", OMNIROUTE_API_KEY], check=True)
    subprocess.run(["hermes", "config", "set", "model.context_length", str(CONTEXT_LENGTH)], check=True)
    subprocess.run(["hermes", "config", "set", "model.aliases.flash", MODEL_DEFAULT], check=True)
    subprocess.run(["hermes", "config", "set", "model.aliases.sonnet", "antigravity/claude-sonnet-4-6"], check=True)
    subprocess.run(["hermes", "config", "set", "model.aliases.opus", "antigravity/claude-opus-4-6-thinking"], check=True)
    print("✅ Hermes profile configured with 1M context routing.")


# ── 5. Background Vault Auto-Sync Worker ─────────────────────────────────────
def auto_sync_worker():
    while True:
        time.sleep(180)
        try:
            subprocess.run(["git", "-C", VAULT_DIR, "add", "."], check=True)
            res = subprocess.run(["git", "-C", VAULT_DIR, "commit", "-m", "Auto-sync from Aethelgard 24/7 Custodian"], capture_output=True, text=True)
            if "nothing to commit" not in res.stdout:
                subprocess.run(["git", "-C", VAULT_DIR, "push", "origin", VAULT_BRANCH], check=True)
                print(f"[Vault Sync] Changes pushed to GitHub: {time.strftime('%H:%M:%S')}", flush=True)
        except Exception as e:
            print(f"[Vault Sync Notice] {e}", flush=True)


# ── 6. Send Telegram Startup Notification ────────────────────────────────────
def send_telegram_startup():
    try:
        # Clear stale updates
        urllib.request.urlopen(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=true", timeout=5)
        urllib.request.urlopen(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset=-1", timeout=5)

        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        msg = (
            f"🏛️ Aethelgard 24/7 Vault Custodian is ONLINE!\n\n"
            f"🧠 Brain: {MODEL_DEFAULT}\n"
            f"📏 Context Window: {CONTEXT_LENGTH:,} tokens (1 Million)\n"
            f"⚡ Speed: 150+ tokens/sec (Instantaneous)\n"
            f"🎙️ Multimodal Hearing: Voice notes & audio translation ready\n"
            f"📁 Obsidian Vault: Synchronized with GitHub\n"
            f"🌐 Infrastructure: 24/7 Always-On Virtual Machine"
        )
        data = json.dumps({"chat_id": TELEGRAM_USER_ID, "text": msg}).encode("utf-8")
        req = urllib.request.Request(tg_url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                print("📱 Telegram startup ping delivered to Omar (@aethelgard_vault_bot)!")
    except Exception as e:
        print(f"⚠ Telegram startup notice: {e}")


# ── 7. Run Hermes Gateway 24/7 Loop ──────────────────────────────────────────
def run_gateway_forever():
    print("\n" + "=" * 65)
    print("🚀 [4/4] LAUNCHING HERMES GATEWAY (24/7 ALWAYS-ON SUPERVISION)")
    print("=" * 65)

    threading.Thread(target=auto_sync_worker, daemon=True).start()
    send_telegram_startup()

    cmd = ["hermes", "-p", HERMES_PROFILE, "gateway", "run", "--replace", "--force", "--accept-hooks"]

    while True:
        try:
            print(f"[{time.strftime('%H:%M:%S')}] Starting Hermes Gateway process...", flush=True)
            proc = subprocess.Popen(cmd, env=dict(os.environ))
            proc.wait()
            print(f"⚠ Hermes Gateway exited with code {proc.returncode}. Restarting in 5 seconds...", flush=True)
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n🛑 Shutdown signal received. Exiting gracefully...", flush=True)
            break
        except Exception as ge:
            print(f"⚠ Gateway exception: {ge}. Restarting in 10 seconds...", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    init_vault()
    init_omniroute()
    init_hermes()
    run_gateway_forever()
