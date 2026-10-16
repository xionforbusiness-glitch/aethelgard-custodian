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
from antigravity_token_manager import (
    sync_token_to_sqlite,
    start_token_maintainer_thread,
    patch_omniroute_antigravity_catalog,
)

# Ensure UTF-8 stdout/stderr on all terminals
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
raw_vault = os.environ.get("VAULT_DIR", "~/vault")
if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0 and raw_vault.startswith("/root"):
    raw_vault = "~/vault"
VAULT_DIR = os.path.abspath(os.path.expanduser(raw_vault))

STORAGE_ENCRYPTION_KEY = os.environ.get("STORAGE_ENCRYPTION_KEY", "0000000000000000000000000000000000000000000000000000000000000000")
OMNIROUTE_PORT = os.environ.get("OMNIROUTE_PORT", "20128")
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", "sk-omniroute-redacted")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

HERMES_PROFILE = "llm-wiki"
HERMES_HOME = os.path.expanduser("~/.hermes")
HERMES_PROFILE_DIR = os.path.join(HERMES_HOME, "profiles", HERMES_PROFILE)
OMNIROUTE_DIR = os.path.expanduser("~/.omniroute")
MODEL_DEFAULT = os.environ.get("HERMES_MODEL", "antigravity/gemini-3.8-flash-low")
CONTEXT_LENGTH = int(os.environ.get("HERMES_CONTEXT_LENGTH", "1048576"))

print("\n" + "=" * 65)
print("🏛️ AETHELGARD 24/7 AUTONOMOUS VAULT CUSTODIAN INITIALIZING...")
print("=" * 65)
print(f"🧠 Brain Model:       {MODEL_DEFAULT}")
print(f"📏 Context Window:    {CONTEXT_LENGTH:,} tokens (1M Instant Low-Latency)")
print(f"🌐 Antigravity Plan:  Google AI Pro Subscription (OmniRoute :{OMNIROUTE_PORT})")
print(f"📁 Target Vault:      {VAULT_DIR} -> GitHub {GITHUB_USER}/{VAULT_REPO}")
print(f"📱 Telegram Gateway:  @aethelgard_vault_bot")
print("=" * 65 + "\n")


# ── 1. Auto-Update Custodian Codebase & Stale Locks ──────────────────────────
def unlock_stale_hermes_sessions():
    """
    Guarantees 100% gateway responsiveness:
    1. Removes all stale lock and heartbeat markers.
    2. Clears active_turn_token, active_turn_started_at, resume_pending from gateway_routing
       in ~/.hermes/state.db and profile state.db databases.
    3. Clears active_turn_token and resume_pending from all sessions.json files.
    This permanently prevents Hermes from getting stuck in an unresponsive 'resume_pending' or
    locked state after watchdog resets or abrupt restarts.
    """
    print("🔓 [Session Armor] Checking & unlocking any stale session locks...")

    # 1. Purge ephemeral lock files across all locations
    hb_path = "/tmp/hermes_turn_heartbeat.json" if os.name != "nt" else os.path.join(SCRIPT_DIR, "heartbeat.json")
    if os.path.exists(hb_path):
        try:
            os.remove(hb_path)
        except Exception:
            pass

    restart_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(SCRIPT_DIR, "restart.trigger")
    if os.path.exists(restart_path):
        try:
            os.remove(restart_path)
        except Exception:
            pass

    lock_dirs = [HERMES_HOME, HERMES_PROFILE_DIR]
    profiles_root = os.path.join(HERMES_HOME, "profiles")
    if os.path.exists(profiles_root):
        for p_name in os.listdir(profiles_root):
            p_dir = os.path.join(profiles_root, p_name)
            if os.path.isdir(p_dir) and p_dir not in lock_dirs:
                lock_dirs.append(p_dir)

    for ld in lock_dirs:
        for lock_f in ["gateway.lock", "gateway_state.json", "gateway-dispatcher.lock",
                       ".update_prompt.json", ".update_response", ".drain_request.json"]:
            lp = os.path.join(ld, lock_f)
            if os.path.exists(lp):
                try:
                    os.remove(lp)
                except Exception:
                    pass

    # 2. Unlock SQLite gateway_routing table in state.db files
    db_candidates = [
        os.path.join(HERMES_HOME, "state.db"),
        os.path.join(HERMES_PROFILE_DIR, "state.db"),
    ]
    if os.path.exists(profiles_root):
        for p_name in os.listdir(profiles_root):
            p_db = os.path.join(profiles_root, p_name, "state.db")
            if os.path.exists(p_db) and p_db not in db_candidates:
                db_candidates.append(p_db)

    for db_p in db_candidates:
        if not os.path.exists(db_p):
            continue
        try:
            conn = sqlite3.connect(db_p, timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gateway_routing';")
            if cur.fetchone():
                cur.execute("SELECT scope, session_key, entry_json FROM gateway_routing;")
                rows = cur.fetchall()
                cleared = 0
                for scope, session_key, entry_json in rows:
                    try:
                        entry = json.loads(entry_json)
                        changed = False
                        for field, default in [("active_turn_token", None), ("active_turn_started_at", None),
                                               ("resume_pending", False), ("resume_reason", None)]:
                            if entry.get(field) != default:
                                entry[field] = default
                                changed = True
                        if changed:
                            cur.execute(
                                "UPDATE gateway_routing SET entry_json = ? WHERE scope = ? AND session_key = ?",
                                (json.dumps(entry), scope, session_key)
                            )
                            cleared += 1
                    except Exception:
                        pass
                if cleared > 0:
                    conn.commit()
                    print(f"  ✅ Unlocked {cleared} session(s) in {os.path.basename(db_p)}.")
            conn.close()
        except Exception as dbe:
            print(f"  ⚠ Notice unlocking {db_p}: {dbe}")

    # 3. Unlock JSON files (sessions.json)
    json_candidates = [
        os.path.join(HERMES_HOME, "sessions", "sessions.json"),
        os.path.join(HERMES_PROFILE_DIR, "sessions", "sessions.json"),
    ]
    if os.path.exists(profiles_root):
        for p_name in os.listdir(profiles_root):
            p_json = os.path.join(profiles_root, p_name, "sessions", "sessions.json")
            if os.path.exists(p_json) and p_json not in json_candidates:
                json_candidates.append(p_json)

    for jp in json_candidates:
        if not os.path.exists(jp):
            continue
        try:
            with open(jp, "r", encoding="utf-8") as jf:
                data = json.load(jf)
            changed = False
            if isinstance(data, dict):
                for k, entry in data.items():
                    if isinstance(entry, dict):
                        for field, default in [("active_turn_token", None), ("active_turn_started_at", None),
                                               ("resume_pending", False), ("resume_reason", None)]:
                            if entry.get(field) != default:
                                entry[field] = default
                                changed = True
            if changed:
                with open(jp, "w", encoding="utf-8") as jf:
                    json.dump(data, jf, indent=2)
                print(f"  ✅ Sanitized active turn tokens in {os.path.basename(jp)}.")
        except Exception as je:
            print(f"  ⚠ Notice sanitizing {jp}: {je}")

    # 4. Ensure Gateway Voice Mode is configured to 'all' so voice notes generate with each message
    voice_mode_path = os.path.join(HERMES_HOME, "gateway_voice_mode.json")
    try:
        os.makedirs(os.path.dirname(voice_mode_path), exist_ok=True)
        v_data = {
            f"{HERMES_PROFILE}:telegram:{TELEGRAM_USER_ID}": "all",
            f"telegram:{TELEGRAM_USER_ID}": "all",
        }
        with open(voice_mode_path, "w", encoding="utf-8") as vf:
            json.dump(v_data, vf, indent=2)
    except Exception:
        pass


def auto_update_self():
    """Auto-pull latest codebase on boot and clear any stale lock files."""
    print("🔄 Checking for Aethelgard Custodian updates from GitHub...")
    if os.path.exists(os.path.join(SCRIPT_DIR, ".git")):
        try:
            res = subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--rebase", "origin", "main"], timeout=15, capture_output=True, text=True)
            if "Already up to date" not in res.stdout:
                print(f"✅ Aethelgard updated to latest commit: {res.stdout.strip()}")
            else:
                print("✅ Aethelgard is up to date.")
        except Exception as e:
            print(f"⚠ Auto-update notice: {e}")

    unlock_stale_hermes_sessions()


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
def is_omniroute_alive() -> bool:
    """Checks if OmniRoute proxy is healthy and responding to requests."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{OMNIROUTE_PORT}/v1/models",
            headers={"Authorization": f"Bearer {OMNIROUTE_API_KEY}"}
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_omniroute_if_needed():
    """Starts OmniRoute daemon if it is not currently responsive."""
    if not is_omniroute_alive():
        print("🔄 OmniRoute is offline or stopped. Restarting OmniRoute daemon...", flush=True)
        init_omniroute()


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

    # Apply OmniRoute v3.8.52 Antigravity catalog & Claude 5.5 hot-patches before starting/using OmniRoute
    try:
        patch_omniroute_antigravity_catalog()
    except Exception as pe:
        print(f"⚠ OmniRoute catalog patch notice: {pe}", flush=True)

    db_dest = os.path.join(OMNIROUTE_DIR, "storage.sqlite")
    if is_omniroute_alive():
        print("✅ OmniRoute is already running and healthy on port 20128!")
        try:
            sync_token_to_sqlite(db_dest)
        except Exception as te:
            print(f"⚠ Token refresh notice: {te}", flush=True)
        start_token_maintainer_thread(db_dest, interval_sec=1200)
    else:
        # Stop any conflicting process on port 20128 first
        subprocess.run(f"omniroute stop 2>/dev/null; pkill -9 -f omniroute 2>/dev/null; fuser -k {OMNIROUTE_PORT}/tcp 2>/dev/null || true", shell=True)
        time.sleep(2)

        # Clean stale SQLite WAL/SHM files to prevent corruption
        for extra in ["storage.sqlite-wal", "storage.sqlite-shm", "storage.sqlite-journal"]:
            p = os.path.join(OMNIROUTE_DIR, extra)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

        # Restore pre-verified SQLite database containing Antigravity Pro credentials
        db_gz = os.path.join(SCRIPT_DIR, "omniroute", "storage.sqlite.gz")
        if os.path.exists(db_gz):
            print(f"📦 Decompressing Antigravity Pro credentials database from {db_gz}...")
            with gzip.open(db_gz, "rb") as f_in, open(db_dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            print("✅ Database restored.")

        # Proactively ensure Antigravity OAuth access token is fresh BEFORE starting daemon
        try:
            sync_token_to_sqlite(db_dest)
        except Exception as te:
            print(f"⚠ Token refresh notice: {te}", flush=True)

        start_token_maintainer_thread(db_dest, interval_sec=1200)

        # Start OmniRoute daemon via 'omniroute serve'
        env = dict(os.environ)
        env["STORAGE_ENCRYPTION_KEY"] = STORAGE_ENCRYPTION_KEY
        env["OMNIROUTE_MAX_PENDING_MIGRATIONS"] = "0"
        env["PORT"] = str(OMNIROUTE_PORT)
        env["NODE_OPTIONS"] = "--dns-result-order=ipv4first --max-old-space-size=1536"

        log_path = "/tmp/omniroute.log" if os.name != "nt" else os.path.join(SCRIPT_DIR, "omniroute.log")
        log_f = open(log_path, "w", encoding="utf-8")
        subprocess.Popen(["omniroute", "serve"], env=env, stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True)

        # Health check: wait up to 180 seconds on e2-micro (CPU throttled cold boot)
        print(f"⏳ Waiting for OmniRoute on http://127.0.0.1:{OMNIROUTE_PORT} (up to 3 min for cold boot)...")
        ready = False
        start_wait = time.time()
        for i in range(90):
            if is_omniroute_alive():
                ready = True
                break
            time.sleep(2)
            elapsed = int(time.time() - start_wait)
            if (i + 1) % 10 == 0:
                print(f"   ...still initializing OmniRoute ({elapsed}s elapsed)...", flush=True)

        if not ready:
            print(f"❌ Failed to connect to OmniRoute after {int(time.time() - start_wait)}s. Recent log from {log_path}:")
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                    for line in lf.readlines()[-25:]:
                        print(f"  [OmniRoute] {line.strip()}")
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

    # Sync config.yaml, SOUL.md & skills
    cfg_src = os.path.join(SCRIPT_DIR, "profile", "config.yaml")
    if os.path.exists(cfg_src):
        shutil.copy2(cfg_src, os.path.join(HERMES_PROFILE_DIR, "config.yaml"))

    soul_src = os.path.join(SCRIPT_DIR, "profile", "SOUL.md")
    if os.path.exists(soul_src):
        shutil.copy2(soul_src, os.path.join(HERMES_PROFILE_DIR, "SOUL.md"))

    skills_src = os.path.join(SCRIPT_DIR, "profile", "skills")
    if os.path.exists(skills_src):
        skills_dest = os.path.join(HERMES_PROFILE_DIR, "skills")
        if os.path.exists(skills_dest):
            shutil.rmtree(skills_dest, ignore_errors=True)
        shutil.copytree(skills_src, skills_dest)

    # Sync hooks (Shift sync, Excel delivery, Watchdog heartbeat)
    hooks_src = os.path.join(SCRIPT_DIR, "profile", "hooks")
    if os.path.exists(hooks_src):
        for target_dir in [os.path.join(HERMES_PROFILE_DIR, "hooks"), os.path.join(HERMES_HOME, "hooks")]:
            os.makedirs(target_dir, exist_ok=True)
            for h_name in os.listdir(hooks_src):
                h_s = os.path.join(hooks_src, h_name)
                h_d = os.path.join(target_dir, h_name)
                if os.path.isdir(h_s):
                    if os.path.exists(h_d):
                        shutil.rmtree(h_d, ignore_errors=True)
                    shutil.copytree(h_s, h_d)

    # Purge stale caches (remove any auto/best-coding leaks)
    for cache_f in ["provider_models_cache.json", "context_length_cache.yaml"]:
        cp = os.path.join(HERMES_PROFILE_DIR, cache_f)
        if os.path.exists(cp):
            try:
                os.remove(cp)
            except Exception:
                pass

    stt_script = os.path.join(SCRIPT_DIR, "transcribe_gemini.py")
    stt_cmd = f"python3 {stt_script} {{input_path}} --output_dir {{output_dir}}"

    # Write profile .env
    with open(os.path.join(HERMES_PROFILE_DIR, ".env"), "w", encoding="utf-8") as f:
        f.write(f"TELEGRAM_BOT_TOKEN={TELEGRAM_BOT_TOKEN}\n")
        f.write(f"TELEGRAM_ALLOWED_USERS={TELEGRAM_USER_ID}\n")
        f.write("GATEWAY_ALLOW_ALL_USERS=true\n")
        f.write(f"WIKI_PATH={VAULT_DIR}\n")
        f.write(f"OBSIDIAN_VAULT_PATH={VAULT_DIR}\n")
        f.write(f"HERMES_CUSTOM_FIRST_TIME_API_KEY={OMNIROUTE_API_KEY}\n")
        f.write(f"OMNIROUTE_API_KEY={OMNIROUTE_API_KEY}\n")
        if GOOGLE_API_KEY:
            f.write(f"GOOGLE_API_KEY={GOOGLE_API_KEY}\n")
            f.write(f"GEMINI_API_KEY={GOOGLE_API_KEY}\n")
        f.write(f"HERMES_LOCAL_STT_COMMAND={stt_cmd}\n")
        f.write(f"AETHELGARD_DIR={SCRIPT_DIR}\n")

    if GOOGLE_API_KEY:
        home_env = os.path.join(HERMES_HOME, ".env")
        try:
            os.makedirs(HERMES_HOME, exist_ok=True)
            with open(home_env, "a", encoding="utf-8") as hef:
                hef.write(f"\nGOOGLE_API_KEY={GOOGLE_API_KEY}\nGEMINI_API_KEY={GOOGLE_API_KEY}\n")
        except Exception:
            pass

    os.environ["WIKI_PATH"] = VAULT_DIR
    os.environ["OBSIDIAN_VAULT_PATH"] = VAULT_DIR
    os.environ["TELEGRAM_BOT_TOKEN"] = TELEGRAM_BOT_TOKEN
    os.environ["TELEGRAM_ALLOWED_USERS"] = TELEGRAM_USER_ID
    os.environ["GATEWAY_ALLOW_ALL_USERS"] = "true"
    os.environ["HERMES_PROFILE"] = HERMES_PROFILE
    os.environ["HERMES_HOME"] = HERMES_HOME
    os.environ["HERMES_CUSTOM_FIRST_TIME_API_KEY"] = OMNIROUTE_API_KEY
    os.environ["OMNIROUTE_API_KEY"] = OMNIROUTE_API_KEY
    if GOOGLE_API_KEY:
        os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
        os.environ["GEMINI_API_KEY"] = GOOGLE_API_KEY
    else:
        os.environ.pop("GOOGLE_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
    os.environ["HERMES_LOCAL_STT_COMMAND"] = stt_cmd
    os.environ["AETHELGARD_DIR"] = SCRIPT_DIR
    os.environ["NODE_OPTIONS"] = "--dns-result-order=ipv4first --max-old-space-size=1536"

    # Apply hermes CLI configurations safely
    try:
        commands = [
            ["hermes", "profile", "use", HERMES_PROFILE],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.provider", "first-time"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.default", MODEL_DEFAULT],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.base_url", f"http://127.0.0.1:{OMNIROUTE_PORT}/v1"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.streaming", "true"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "providers.first-time.streaming", "true"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "voice.auto_tts", "true"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "tts.provider", "edge"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "tts.voice", "en-GB-RyanNeural"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.key_env", "HERMES_CUSTOM_FIRST_TIME_API_KEY"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.context_length", str(CONTEXT_LENGTH)],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "providers.first-time.model", MODEL_DEFAULT],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "providers.first-time.discover_models", "false"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "terminal.timeout", "300"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "slash_commands.confirm_new", "false"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "slash_commands.confirm_reset", "false"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "compression.enabled", "false"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "stt.provider", "local_command"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.flash", "antigravity/gemini-3.8-flash-low"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.flash-low", "antigravity/gemini-3.8-flash-low"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.gemini", "antigravity/gemini-3.8-flash-low"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.fast", "antigravity/gemini-3.8-flash-low"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.chat", "antigravity/gemini-3.8-flash-low"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.casual", "antigravity/gemini-3.8-flash-low"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.low", "antigravity/gemini-3.8-flash-low"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.medium", "antigravity/gemini-3.8-flash-medium"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.flash-medium", "antigravity/gemini-3.8-flash-medium"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.custodian", "antigravity/gemini-3.8-flash-high"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.vault", "antigravity/gemini-3.8-flash-high"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.high", "antigravity/gemini-3.8-flash-high"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.thinking", "antigravity/gemini-3.8-flash-high"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.flash-high", "antigravity/gemini-3.8-flash-high"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.pro", "antigravity/gemini-3.8-flash-high"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.claude", "antigravity/claude-sonnet-5-5-high"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.sonnet", "antigravity/claude-sonnet-5-5-high"],
            ["hermes", "-p", HERMES_PROFILE, "config", "set", "model.aliases.opus", "antigravity/claude-opus-5-5-high"],
        ]
        for cmd in commands:
            subprocess.run(cmd, check=False)

        # Authoritative sync of profile/config.yaml (pure Antigravity Pro configuration)
        if os.path.exists(cfg_src):
            shutil.copy2(cfg_src, os.path.join(HERMES_PROFILE_DIR, "config.yaml"))
            print("✅ Hermes config.yaml authoritative sync complete (pure Antigravity Pro configuration).")

        print("✅ Hermes profile configured: LOW mode (sub-second) locked as default brain.")
    except Exception as he:
        print(f"⚠ Hermes config CLI notice: {he}")


# ── 5. Background Vault Auto-Sync & Code Auto-Updater Worker ──────────────────
def auto_sync_worker():
    restart_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(SCRIPT_DIR, "restart.trigger")
    while True:
        time.sleep(45)
        # 1. Codebase Auto-Updater: check if origin/main has new commits
        try:
            subprocess.run(["git", "-C", SCRIPT_DIR, "fetch", "origin", "main"], check=False, capture_output=True, timeout=15)
            status = subprocess.run(["git", "-C", SCRIPT_DIR, "status", "-uno"], capture_output=True, text=True, timeout=10)
            if "Your branch is behind" in status.stdout:
                print("[Code Sync] 🚀 New commits detected on origin/main! Auto-pulling and triggering reload...", flush=True)
                subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--rebase", "origin", "main"], check=False, timeout=15)
                with open(restart_path, "w", encoding="utf-8") as rf:
                    rf.write("code_update")
        except Exception:
            pass

        # 2. Vault Sync
        try:
            # First, pull any new commits from local desktop / GitHub
            subprocess.run(["git", "-C", VAULT_DIR, "pull", "--rebase", "origin", VAULT_BRANCH], check=False)
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
        # Clear stale webhooks cleanly (preserve pending user updates)
        urllib.request.urlopen(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook", timeout=5)

        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        msg = (
            f"🏛️ Aethelgard 24/7 Vault Custodian is ONLINE!\n\n"
            f"🧠 Brain: {MODEL_DEFAULT}\n"
            f"⚡ Speed: Instantaneous Sub-Second Chat\n"
            f"🎙️ Voice Notes: Generating with each message (Edge TTS)\n"
            f"📊 Excel Engine: On-demand timesheet delivery ready (/excel)\n"
            f"📋 Work Tracker: Non-intrusive reminders active\n"
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
active_gateway_proc = None
gateway_start_time = 0.0

def emergency_reboot_worker():
    """Monitors running gateway for remote /restart triggers without killing turns."""
    restart_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(SCRIPT_DIR, "restart.trigger")

    omniroute_fail_count = 0
    while True:
        time.sleep(5)
        global active_gateway_proc
        proc = active_gateway_proc
        if not proc or proc.poll() is not None:
            continue

        # 1. External Emergency Restart Trigger (from /restart, /reboot, or auto-updater)
        if os.path.exists(restart_path):
            try:
                os.remove(restart_path)
            except Exception:
                pass
            print("[Supervisor] 🔄 Emergency restart requested! Pulling latest code & recycling service...", flush=True)
            try:
                subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--rebase", "origin", "main"], timeout=15, check=False)
            except Exception:
                pass
            unlock_stale_hermes_sessions()
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            unlock_stale_hermes_sessions()
            print("[Supervisor] 🚀 Exiting process so systemd performs a clean 100% reload...", flush=True)
            sys.exit(0)

        # 2. OmniRoute proxy liveness check: restart daemon if proxy went down (3 consecutive failures)
        if not is_omniroute_alive():
            omniroute_fail_count += 1
            if omniroute_fail_count >= 3:
                print("[Supervisor] 🚨 OmniRoute proxy unresponsive (3 checks)! Restarting OmniRoute daemon...", flush=True)
                try:
                    subprocess.run(f"pkill -9 -f omniroute 2>/dev/null; fuser -k {OMNIROUTE_PORT}/tcp 2>/dev/null || true", shell=True, check=False)
                except Exception:
                    pass
                start_omniroute_if_needed()
                omniroute_fail_count = 0
        else:
            omniroute_fail_count = 0


def test_antigravity_liveness(timeout_sec: int = 30) -> bool:
    """Sends a test chat completion request to OmniRoute to verify Antigravity Pro liveness."""
    try:
        test_payload = json.dumps({
            "model": MODEL_DEFAULT,
            "messages": [{"role": "user", "content": "Antigravity health ping. Respond with 'OK'."}],
            "max_tokens": 10
        }).encode("utf-8")
        test_req = urllib.request.Request(
            f"http://127.0.0.1:{OMNIROUTE_PORT}/v1/chat/completions",
            data=test_payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {OMNIROUTE_API_KEY}"}
        )
        with urllib.request.urlopen(test_req, timeout=timeout_sec) as resp:
            if resp.status == 200:
                res_json = json.loads(resp.read().decode("utf-8"))
                choice = res_json.get("choices", [{}])[0].get("message", {}).get("content", "")
                return bool(choice)
            return False
    except Exception as e:
        print(f"[Health Check] ⚠ Antigravity liveness check failed: {e}", flush=True)
        return False


def heal_antigravity_service() -> bool:
    """
    Auto-healing procedure for Antigravity:
    1. Refreshes OAuth access & refresh tokens and commits directly into SQLite.
    2. Restarts OmniRoute daemon so fresh tokens and connection pools are loaded.
    3. Re-tests liveness; if still failing, triggers emergency service reload.
    """
    print("🚑 [Auto-Heal] Starting Antigravity auto-healing routine...", flush=True)
    db_dest = os.path.join(OMNIROUTE_DIR, "storage.sqlite")

    # 1. Refresh OAuth token in SQLite
    token_ok = False
    try:
        token_ok = sync_token_to_sqlite(db_dest)
        print(f"[Auto-Heal] Token refresh result: {'SUCCESS' if token_ok else 'FAILED'}", flush=True)
    except Exception as te:
        print(f"[Auto-Heal] ⚠ Token refresh exception: {te}", flush=True)

    # 2. Restart OmniRoute daemon
    try:
        print("[Auto-Heal] Recycling OmniRoute daemon...", flush=True)
        if os.name != "nt":
            subprocess.run(f"omniroute stop 2>/dev/null; pkill -9 -f omniroute 2>/dev/null; fuser -k {OMNIROUTE_PORT}/tcp 2>/dev/null || true", shell=True, check=False)
        else:
            subprocess.run(["taskkill", "/F", "/IM", "omniroute.exe"], capture_output=True)
        time.sleep(2)
        start_omniroute_if_needed()
    except Exception as oe:
        print(f"[Auto-Heal] ⚠ OmniRoute reload exception: {oe}", flush=True)

    # 3. Verify liveness post-heal
    time.sleep(3)
    if test_antigravity_liveness(timeout_sec=30):
        print("🎉 [Auto-Heal] Antigravity Pro service restored and healthy!", flush=True)
        return True
    else:
        print("🚨 [Auto-Heal] Liveness check still failing after reload. Triggering full service restart...", flush=True)
        restart_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(SCRIPT_DIR, "restart.trigger")
        with open(restart_path, "w", encoding="utf-8") as rf:
            rf.write("antigravity_health_check_auto_heal")
        return False


def antigravity_health_check_worker(interval_sec: int = 7200):
    """
    Persistent 2-hour automated health check worker.
    Tests Antigravity liveness every 2 hours and automatically heals
    (refreshes token + reloads service) if any error occurs.
    """
    print(f"🛡️ [Health Supervisor] Antigravity 2-hour automated health check worker active (interval: {interval_sec}s).", flush=True)
    while True:
        time.sleep(interval_sec)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🩺 Running scheduled 2-hour Antigravity health check...", flush=True)
        if test_antigravity_liveness():
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ✅ Antigravity 2-hour health check PASSED. System fully operational.", flush=True)
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚨 Antigravity health check FAILED! Auto-healing initiated...", flush=True)
            heal_antigravity_service()


def run_gateway_forever():
    global active_gateway_proc, gateway_start_time
    print("\n" + "=" * 65)
    print("🚀 [4/4] LAUNCHING HERMES GATEWAY (24/7 ALWAYS-ON SUPERVISION)")
    print("=" * 65)

    threading.Thread(target=auto_sync_worker, daemon=True).start()
    threading.Thread(target=emergency_reboot_worker, daemon=True).start()
    threading.Thread(target=antigravity_health_check_worker, daemon=True, name="AntigravityHealthCheck").start()

    # ── Launch Proactive Scheduler & Second Brain Librarian ──────────────
    try:
        from proactive_scheduler import proactive_scheduler_daemon
        threading.Thread(target=proactive_scheduler_daemon, args=(VAULT_DIR,), daemon=True).start()
        print("⏰ Proactive Shift Scheduler & Second Brain Librarian thread active!")
    except Exception as se:
        print(f"⚠ Proactive Scheduler startup notice: {se}")

    # ── Launch Gemini 3.8 Live Voice Call Bridge ─────────
    try:
        from live_call_server import start_live_call_server_background
        start_live_call_server_background()
        print("📞 Gemini 3.8 Live Voice Call server active on port 20130!")
    except Exception as ce:
        print(f"⚠ Live Call Server startup notice: {ce}")

    send_telegram_startup()

    cmd = ["hermes", "-p", HERMES_PROFILE, "gateway", "run", "--replace", "--force", "--accept-hooks"]

    while True:
        try:
            unlock_stale_hermes_sessions()
            print(f"[{time.strftime('%H:%M:%S')}] Starting Hermes Gateway process...", flush=True)
            gateway_start_time = time.time()
            active_gateway_proc = subprocess.Popen(cmd, env=dict(os.environ))
            active_gateway_proc.wait()
            print(f"⚠ Hermes Gateway exited with code {active_gateway_proc.returncode}. Restarting in 5 seconds...", flush=True)
            unlock_stale_hermes_sessions()
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n🛑 Shutdown signal received. Exiting gracefully...", flush=True)
            break
        except Exception as ge:
            print(f"⚠ Gateway exception: {ge}. Restarting in 5 seconds...", flush=True)
            unlock_stale_hermes_sessions()
            time.sleep(5)


if __name__ == "__main__":
    auto_update_self()
    init_vault()
    init_omniroute()
    init_hermes()
    run_gateway_forever()
