#!/usr/bin/env python3
# ==============================================================================
# 🏛️ AETHELGARD 24/7 AUTONOMOUS CUSTODIAN
# Brain: Google Antigravity Pro Plan (Gemini 3.7 / 3.8 Flash High - 1M Context)
# Interface: Telegram (@aethelgard_vault_bot)
# Persistence: Automated 2-Way Git Sync to Aethelgard Obsidian Vault
# ==============================================================================

import os
import re
import sys
import time
import json
import gzip
import shutil
import sqlite3
import threading
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from antigravity_token_manager import (
    sync_token_to_sqlite,
    start_token_maintainer_thread,
    patch_omniroute_antigravity_catalog,
)
from antigravity_proxy import (
    start_antigravity_proxy_background,
    get_valid_access_token,
)
from gateway_typing_patch import (
    apply_typing_lifecycle_patches,
    patch_hermes_gateway_typing,
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
_STALE_GITHUB_TOKENS = {"ghp_" + "gY0RVq7FifRJQgQVsu8fEsTsi5PV8e45VERo"}


def _resolve_github_token() -> str:
    """Resolves active GitHub PAT from ~/.aethelgard/github_token, env, or git remote URL."""
    token_file = os.path.expanduser("~/.aethelgard/github_token")
    if os.path.isfile(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as tf:
                tok = tf.read().strip()
            if tok and tok not in _STALE_GITHUB_TOKENS:
                return tok
        except Exception:
            pass

    env_tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if env_tok and env_tok not in _STALE_GITHUB_TOKENS:
        return env_tok

    try:
        res = subprocess.run(
            ["git", "-C", SCRIPT_DIR, "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        remote_url = res.stdout.strip()
        parsed = urllib.parse.urlparse(remote_url)
        if parsed.password and parsed.password not in _STALE_GITHUB_TOKENS:
            return parsed.password
    except Exception:
        pass

    return ""


def _ensure_repo_remote_auth(repo_dir: str):
    """Refreshes HTTPS GitHub origin remote URL with the active resolved token."""
    if not repo_dir or not os.path.exists(os.path.join(repo_dir, ".git")):
        return
    tok = _resolve_github_token()
    if not tok:
        return
    try:
        res = subprocess.run(
            ["git", "-C", repo_dir, "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        remote_url = res.stdout.strip()
        if not remote_url:
            return
        parsed = urllib.parse.urlparse(remote_url)
        if parsed.scheme in ("http", "https") and parsed.hostname and parsed.hostname.lower().endswith("github.com") and parsed.path:
            target_url = f"https://{GITHUB_USER}:{tok}@github.com{parsed.path}"
            if remote_url != target_url:
                subprocess.run(
                    ["git", "-C", repo_dir, "remote", "set-url", "origin", target_url],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
    except Exception:
        pass


GITHUB_TOKEN = _resolve_github_token()
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
print(f"🌐 Antigravity Plan:  Google AI Pro Native Gateway (Port :{OMNIROUTE_PORT})")
print(f"📁 Target Vault:      {VAULT_DIR} -> GitHub {GITHUB_USER}/{VAULT_REPO}")
print(f"📱 Telegram Gateway:  @aethelgard_vault_bot")
print("=" * 65 + "\n")


def _get_hermes_roots():
    """Returns active (hermes_home, hermes_profile_dir) pairs across POSIX and Windows native roots."""
    roots = [(HERMES_HOME, HERMES_PROFILE_DIR)]
    if os.name == "nt":
        default_user_home = os.path.normcase(os.path.abspath(os.path.expanduser("~/.hermes")))
        current_home = os.path.normcase(os.path.abspath(HERMES_HOME))
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata and current_home == default_user_home:
            win_home = os.path.join(local_appdata, "hermes")
            win_norm = os.path.normcase(os.path.abspath(win_home))
            if os.path.isdir(win_home) and win_norm != current_home:
                roots.append((win_home, os.path.join(win_home, "profiles", HERMES_PROFILE)))
    return roots


def _strip_deprecated_env_keys(env_path: str) -> None:
    """Removes deprecated TERMINAL_CWD / MESSAGING_CWD entries from an existing .env file."""
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as ef:
            lines = ef.readlines()
        filtered = [
            ln for ln in lines
            if not ln.lstrip().startswith(("TERMINAL_CWD=", "MESSAGING_CWD="))
        ]
        if len(filtered) != len(lines):
            with open(env_path, "w", encoding="utf-8") as ef:
                ef.writelines(filtered)
    except Exception:
        pass


def _write_profile_config(cfg_src: str, target_cfg: str, script_dir: str) -> None:
    """Copies profile/config.yaml and injects OS-native terminal.cwd without polluting git."""
    with open(cfg_src, "r", encoding="utf-8") as cf:
        cfg_text = cf.read()
    cwd_yaml = f"  cwd: {json.dumps(script_dir)}"
    if re.search(r"^terminal:\s*$", cfg_text, re.MULTILINE):
        cfg_text = re.sub(
            r"(^terminal:\s*\r?\n(?:[ \t]+[^\n]*\r?\n)*)",
            lambda m: (
                m.group(1)
                if re.search(r"^[ \t]+cwd:", m.group(1), re.MULTILINE)
                else m.group(1) + cwd_yaml + "\n"
            ),
            cfg_text,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        cfg_text = cfg_text.rstrip() + f"\nterminal:\n{cwd_yaml}\n"
    os.makedirs(os.path.dirname(target_cfg), exist_ok=True)
    with open(target_cfg, "w", encoding="utf-8") as tf:
        tf.write(cfg_text)


def _find_poisoned_session_ids(cur, has_sessions: bool, has_messages: bool, has_system_prompts: bool):
    """Detects pre-omnichannel, refusal-poisoned, or wedged user-only sessions."""
    poisoned_sids = set()
    session_cols = set()
    if has_sessions:
        cur.execute("PRAGMA table_info(sessions);")
        session_cols = {r[1] for r in cur.fetchall()}
        if has_system_prompts and "system_prompt" in session_cols and "system_prompt_hash" in session_cols:
            cur.execute(
                "SELECT s.id FROM sessions s "
                "LEFT JOIN system_prompts sp ON sp.hash = s.system_prompt_hash "
                "WHERE COALESCE(NULLIF(s.system_prompt, ''), sp.prompt, '') != '' "
                "AND COALESCE(NULLIF(s.system_prompt, ''), sp.prompt, '') NOT LIKE '%omnichannel_bridge.py%';"
            )
        elif has_system_prompts and "system_prompt_hash" in session_cols:
            cur.execute(
                "SELECT s.id FROM sessions s "
                "JOIN system_prompts sp ON sp.hash = s.system_prompt_hash "
                "WHERE sp.prompt IS NOT NULL AND sp.prompt != '' "
                "AND sp.prompt NOT LIKE '%omnichannel_bridge.py%';"
            )
        elif "system_prompt" in session_cols:
            cur.execute(
                "SELECT id FROM sessions WHERE system_prompt IS NOT NULL "
                "AND system_prompt != '' AND system_prompt NOT LIKE '%omnichannel_bridge.py%';"
            )
        else:
            cur.execute("SELECT id FROM sessions WHERE 0;")
        for r in cur.fetchall():
            if r[0]:
                poisoned_sids.add(str(r[0]))
        if has_messages and "message_count" in session_cols:
            cur.execute(
                "SELECT s.id FROM sessions s "
                "WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id) "
                "AND (COALESCE(s.message_count, 0) > 0 "
                "     OR (COALESCE(s.system_prompt, '') = '' AND COALESCE(s.system_prompt_hash, '') = ''));"
            )
            for r in cur.fetchall():
                if r[0]:
                    poisoned_sids.add(str(r[0]))

    if has_messages:
        cur.execute("PRAGMA table_info(messages);")
        msg_cols = {r[1] for r in cur.fetchall()}
        if "session_id" in msg_cols and "content" in msg_cols:
            cur.execute(
                "SELECT DISTINCT session_id FROM messages "
                "WHERE content LIKE '%don''t currently have an active integration%' "
                "OR content LIKE '%don''t have an active integration%' "
                "OR content LIKE '%Connect a Google Workspace%' "
                "OR content LIKE '%Set up local API credentials%' "
                "OR content LIKE '%don''t have access to your Gmail%' "
                "OR content LIKE '%can''t check or read your inbox%' "
                "OR content LIKE '%I don''t have your credentials%' "
                "OR content LIKE '%never transfer%' "
                "OR content LIKE '%never receives the actual Gmail%' "
                "OR content LIKE '%stay on your local PC%' "
                "OR content LIKE '%The model provider failed after retries%' "
                "OR content LIKE '%Your request was not processed. Send it again%' "
                "OR content LIKE '%thinking through how to approach%' "
                "OR content LIKE '%Initial attempt to use terminal%' "
                "OR content LIKE '%executed via a code execution tool%' "
                "OR content LIKE '%Gemini 3.5 Flash is no longer available%';"
            )
            for r in cur.fetchall():
                if r[0]:
                    poisoned_sids.add(str(r[0]))
        if "session_id" in msg_cols and "role" in msg_cols:
            cur.execute(
                "SELECT m.session_id FROM messages m "
                "GROUP BY m.session_id "
                "HAVING SUM(CASE WHEN m.role = 'user' THEN 1 ELSE 0 END) > 0 "
                "AND SUM(CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END) = 0;"
            )
            for r in cur.fetchall():
                if r[0]:
                    poisoned_sids.add(str(r[0]))

    return poisoned_sids, session_cols


# ── 1. Auto-Update Custodian Codebase & Stale Locks ──────────────────────────
def unlock_stale_hermes_sessions():
    """
    Guarantees 100% gateway responsiveness:
    1. Removes all stale lock and heartbeat markers.
    2. Clears active_turn_token, active_turn_started_at, resume_pending, AND stale model_override
       from gateway_routing and sessions tables in state.db across active roots.
    3. Purges poisoned/wedged sessions (including prompts stored via system_prompts.prompt join),
       clears cached system prompts, and prunes dangling session IDs from gateway_routing & sessions.json.
    """
    print("🔓 [Session Armor] Checking & unlocking any stale session locks & legacy model overrides...")

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

    roots = _get_hermes_roots()
    lock_dirs = []
    db_candidates = []
    json_candidates = []
    for root_home, root_profile in roots:
        for base_dir in (root_home, root_profile):
            if base_dir not in lock_dirs:
                lock_dirs.append(base_dir)
            db_p = os.path.join(base_dir, "state.db")
            if db_p not in db_candidates:
                db_candidates.append(db_p)
            js_p = os.path.join(base_dir, "sessions", "sessions.json")
            if js_p not in json_candidates:
                json_candidates.append(js_p)

        profiles_root = os.path.join(root_home, "profiles")
        if os.path.exists(profiles_root):
            for p_name in os.listdir(profiles_root):
                p_dir = os.path.join(profiles_root, p_name)
                if os.path.isdir(p_dir):
                    if p_dir not in lock_dirs:
                        lock_dirs.append(p_dir)
                    p_db = os.path.join(p_dir, "state.db")
                    if os.path.exists(p_db) and p_db not in db_candidates:
                        db_candidates.append(p_db)
                    p_json = os.path.join(p_dir, "sessions", "sessions.json")
                    if os.path.exists(p_json) and p_json not in json_candidates:
                        json_candidates.append(p_json)

    for ld in lock_dirs:
        for lock_f in ["gateway.lock", "gateway_state.json", "gateway-dispatcher.lock",
                       ".update_prompt.json", ".update_response", ".drain_request.json",
                       ".skills_prompt_snapshot.json"]:
            lp = os.path.join(ld, lock_f)
            if os.path.exists(lp):
                try:
                    os.remove(lp)
                except Exception:
                    pass

    # 2. Unlock SQLite gateway_routing & sessions tables in state.db files (Pass 1)
    all_poisoned_sids = set()
    all_valid_sids = set()
    any_db_has_sessions = False

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
                        for field, default in [
                            ("active_turn_token", None),
                            ("active_turn_started_at", None),
                            ("resume_pending", False),
                            ("resume_reason", None),
                            ("model_override", None),
                        ]:
                            if entry.get(field) != default:
                                entry[field] = default
                                changed = True
                        if isinstance(entry.get("model"), str) and entry["model"] != MODEL_DEFAULT:
                            entry["model"] = MODEL_DEFAULT
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
                    print(f"  ✅ Unlocked & sanitized {cleared} session(s) in {os.path.basename(db_p)}.")

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions';")
            has_sessions = bool(cur.fetchone())
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages';")
            has_messages = bool(cur.fetchone())
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='system_prompts';")
            has_system_prompts = bool(cur.fetchone())

            poisoned_sids, session_cols = _find_poisoned_session_ids(
                cur, has_sessions, has_messages, has_system_prompts
            )

            if poisoned_sids:
                all_poisoned_sids.update(poisoned_sids)
                try:
                    placeholders = ",".join("?" for _ in poisoned_sids)
                    params = list(poisoned_sids)
                    if has_messages:
                        cur.execute(f"DELETE FROM messages WHERE session_id IN ({placeholders});", params)
                    for aux_tbl in ("session_model_usage", "compression_locks", "session_turn_leases", "async_delegations"):
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (aux_tbl,))
                        if cur.fetchone():
                            cur.execute(f"PRAGMA table_info({aux_tbl});")
                            aux_cols = {r[1] for r in cur.fetchall()}
                            for ref_col in (
                                "session_id",
                                "conversation_id",
                                "parent_session_id",
                                "origin_session_id",
                                "origin_ui_session_id",
                                "origin_session",
                            ):
                                if ref_col in aux_cols:
                                    cur.execute(f"DELETE FROM {aux_tbl} WHERE {ref_col} IN ({placeholders});", params)
                    if has_sessions:
                        if "parent_session_id" in session_cols:
                            cur.execute(
                                f"UPDATE sessions SET parent_session_id = NULL WHERE parent_session_id IN ({placeholders});",
                                params,
                            )
                        cur.execute(f"DELETE FROM sessions WHERE id IN ({placeholders});", params)
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gateway_routing';")
                    if cur.fetchone():
                        cur.execute("DELETE FROM gateway_routing;")
                    conn.commit()
                    print(f"  ✅ Purged {len(poisoned_sids)} poisoned/legacy session(s) in {os.path.basename(db_p)}.")
                except Exception as pe:
                    print(f"  ⚠ Notice purging poisoned sessions in {os.path.basename(db_p)}: {pe}")

            if has_sessions:
                any_db_has_sessions = True
                try:
                    cur.execute("SELECT id FROM sessions;")
                    for r in cur.fetchall():
                        if r[0]:
                            all_valid_sids.add(str(r[0]))
                    set_parts = ["model = ?"]
                    set_vals = [MODEL_DEFAULT]
                    for opt_col in ("system_prompt", "system_prompt_hash", "tool_names"):
                        if opt_col in session_cols:
                            set_parts.append(f"{opt_col} = NULL")
                    cur.execute(f"UPDATE sessions SET {', '.join(set_parts)};", set_vals)
                    conn.commit()
                except Exception:
                    pass

            if has_system_prompts:
                try:
                    cur.execute("DELETE FROM system_prompts;")
                    conn.commit()
                except Exception:
                    pass
            conn.close()
        except Exception as dbe:
            print(f"  ⚠ Notice unlocking {db_p}: {dbe}")

    # Pass 2: Prune dangling gateway_routing entries whose session_id no longer exists in sessions
    for db_p in db_candidates:
        if not os.path.exists(db_p):
            continue
        try:
            conn = sqlite3.connect(db_p, timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gateway_routing';")
            if cur.fetchone():
                cur.execute("SELECT scope, session_key, entry_json FROM gateway_routing;")
                dangling = 0
                for scope, session_key, entry_json in cur.fetchall():
                    try:
                        entry = json.loads(entry_json)
                        sid = entry.get("session_id")
                        if isinstance(sid, str) and sid and (
                            sid in all_poisoned_sids or (any_db_has_sessions and sid not in all_valid_sids)
                        ):
                            cur.execute(
                                "DELETE FROM gateway_routing WHERE scope = ? AND session_key = ?",
                                (scope, session_key),
                            )
                            dangling += 1
                    except Exception:
                        pass
                if dangling > 0:
                    conn.commit()
                    print(f"  ✅ Pruned {dangling} dangling route(s) from gateway_routing in {os.path.basename(db_p)}.")
            conn.close()
        except Exception:
            pass

    # 3. Unlock & prune JSON files (sessions.json) + orphaned session JSONL transcripts
    for jp in json_candidates:
        sessions_dir = os.path.dirname(jp)
        for psid in all_poisoned_sids:
            jsonl_p = os.path.join(sessions_dir, f"{psid}.jsonl")
            if os.path.exists(jsonl_p):
                try:
                    os.remove(jsonl_p)
                except Exception:
                    pass

        if not os.path.exists(jp):
            continue
        try:
            with open(jp, "r", encoding="utf-8") as jf:
                data = json.load(jf)
            changed = False
            if isinstance(data, dict):
                keys_to_remove = []
                for k, entry in data.items():
                    if k.startswith("_") or not isinstance(entry, dict):
                        continue
                    sid = entry.get("session_id")
                    if isinstance(sid, str) and sid and (
                        sid in all_poisoned_sids or (any_db_has_sessions and sid not in all_valid_sids)
                    ):
                        keys_to_remove.append(k)
                        changed = True
                        continue
                    for field, default in [
                        ("active_turn_token", None),
                        ("active_turn_started_at", None),
                        ("resume_pending", False),
                        ("resume_reason", None),
                        ("model_override", None),
                    ]:
                        if entry.get(field) != default:
                            entry[field] = default
                            changed = True
                    if isinstance(entry.get("model"), str) and entry["model"] != MODEL_DEFAULT:
                        entry["model"] = MODEL_DEFAULT
                        changed = True
                for rk in keys_to_remove:
                    del data[rk]
            if changed:
                with open(jp, "w", encoding="utf-8") as jf:
                    json.dump(data, jf, indent=2)
                print(f"  ✅ Sanitized active turn tokens, dangling routes & model overrides in {os.path.basename(jp)}.")
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


def auto_update_self(allow_reexec: bool = False):
    """Auto-pull latest codebase on boot using git reset --hard origin/main so dirty files never block updates."""
    print("🔄 Checking for Aethelgard Custodian updates from GitHub...")
    updated = False
    if os.path.exists(os.path.join(SCRIPT_DIR, ".git")):
        try:
            _ensure_repo_remote_auth(SCRIPT_DIR)
            subprocess.run(["git", "-C", SCRIPT_DIR, "fetch", "origin", "main"], timeout=15, capture_output=True, text=True)
            local_rev = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-parse", "HEAD"], timeout=5, capture_output=True, text=True).stdout.strip()
            remote_rev = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-parse", "origin/main"], timeout=5, capture_output=True, text=True).stdout.strip()
            if local_rev and remote_rev and local_rev != remote_rev:
                res = subprocess.run(["git", "-C", SCRIPT_DIR, "reset", "--hard", "origin/main"], timeout=15, capture_output=True, text=True)
                print(f"✅ Aethelgard updated to latest commit: {res.stdout.strip()}")
                updated = True
            else:
                print("✅ Aethelgard is up to date.")
        except Exception as e:
            print(f"⚠ Auto-update notice: {e}")

    unlock_stale_hermes_sessions()
    if updated and allow_reexec and os.environ.get("_AETHELGARD_BOOT_REEXEC") != "1":
        print("🔄 Reloading updated custodian modules via execv...", flush=True)
        os.environ["_AETHELGARD_BOOT_REEXEC"] = "1"
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)


# ── 2. Sync Aethelgard Obsidian Vault from GitHub ────────────────────────────
def _abort_stale_git_rebase(repo_dir: str) -> None:
    """Aborts or clears leftover git rebase state directories before pull --rebase."""
    git_dir = os.path.join(repo_dir, ".git")
    for name in ("rebase-merge", "rebase-apply"):
        if os.path.exists(os.path.join(git_dir, name)):
            subprocess.run(["git", "-C", repo_dir, "rebase", "--abort"], check=False, capture_output=True, timeout=10)
            stale = os.path.join(git_dir, name)
            if os.path.exists(stale):
                shutil.rmtree(stale, ignore_errors=True)


def init_vault():
    print("📁 [1/4] Synchronizing Aethelgard Obsidian Vault...")
    os.makedirs(os.path.dirname(VAULT_DIR) or ".", exist_ok=True)
    repo_name = REPO_NAME if "REPO_NAME" in globals() else VAULT_REPO
    auth_url = (
        f"https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git"
        if GITHUB_TOKEN
        else f"https://github.com/{GITHUB_USER}/{repo_name}.git"
    )
    if os.path.exists(VAULT_DIR) and os.path.exists(os.path.join(VAULT_DIR, ".git")):
        try:
            if GITHUB_TOKEN:
                subprocess.run(["git", "-C", VAULT_DIR, "remote", "set-url", "origin", auth_url], check=False)
            _abort_stale_git_rebase(VAULT_DIR)
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


# ── 3. Initialize Native Antigravity Pro OpenAI Gateway (Port 20128) ─────────
def is_omniroute_alive() -> bool:
    """Checks if our Native Antigravity Proxy on port 20128 is healthy."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{OMNIROUTE_PORT}/health",
            headers={"Authorization": f"Bearer {OMNIROUTE_API_KEY}"}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                return data.get("engine") == "aethelgard-antigravity-native-proxy"
        return False
    except Exception:
        return False


def start_omniroute_if_needed():
    """Starts the Native Antigravity Proxy if it is not currently responsive."""
    if not is_omniroute_alive():
        print("🔄 Starting Native Antigravity Gateway on port 20128...", flush=True)
        init_omniroute()


def init_omniroute():
    print("\n🧠 [2/4] Initializing Native Google Antigravity Pro Plan Gateway (Zero-Lockout Engine)...")
    if not is_omniroute_alive():
        # Terminate any legacy Node.js omniroute process holding port 20128
        if os.name != "nt":
            subprocess.run(
                f"omniroute stop 2>/dev/null; pkill -9 -f omniroute 2>/dev/null; fuser -k {OMNIROUTE_PORT}/tcp 2>/dev/null || true",
                shell=True,
                check=False,
            )
            time.sleep(1)

        # Warm up OAuth token cache and start in-process multi-threaded proxy server
        get_valid_access_token(force_refresh=True)
        start_antigravity_proxy_background(int(OMNIROUTE_PORT))
        time.sleep(0.5)

    # Smoke test Antigravity Pro Gemini 3.8 Flash Low
    print(f"🧪 Smoke testing {MODEL_DEFAULT} via Native Antigravity Pro Gateway...")
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
def _sync_hermes_profile_files(root_homes=None, root_profiles=None):
    """Synchronizes SOUL.md, config.yaml, skills, hooks, career assets, and .env to active Hermes roots."""
    if root_homes is None or root_profiles is None:
        roots = _get_hermes_roots()
        root_homes = []
        root_profiles = []
        for root_home, root_profile in roots:
            os.makedirs(root_home, exist_ok=True)
            os.makedirs(root_profile, exist_ok=True)
            if root_home not in root_homes:
                root_homes.append(root_home)
            if root_profile not in root_profiles:
                root_profiles.append(root_profile)

    # Sync config.yaml, SOUL.md & skills across active roots/profiles
    cfg_src = os.path.join(SCRIPT_DIR, "profile", "config.yaml")
    if os.path.exists(cfg_src):
        for target_profile in root_profiles:
            _write_profile_config(cfg_src, os.path.join(target_profile, "config.yaml"), SCRIPT_DIR)

    soul_src = os.path.join(SCRIPT_DIR, "profile", "SOUL.md")
    if os.path.exists(soul_src):
        for target_home in root_homes + root_profiles:
            shutil.copy2(soul_src, os.path.join(target_home, "SOUL.md"))

    skills_src = os.path.join(SCRIPT_DIR, "profile", "skills")
    if os.path.exists(skills_src):
        for target_home in root_homes + root_profiles:
            target_skills = os.path.join(target_home, "skills")
            if os.path.exists(target_skills):
                shutil.rmtree(target_skills, ignore_errors=True)
            shutil.copytree(skills_src, target_skills)

    # Purge cached skills snapshot across both profile and root homes
    for snap_home in root_homes + root_profiles:
        snap_p = os.path.join(snap_home, ".skills_prompt_snapshot.json")
        if os.path.exists(snap_p):
            try:
                os.remove(snap_p)
            except Exception:
                pass

    # Sync hooks (Shift sync, Excel delivery, Watchdog heartbeat)
    hooks_src = os.path.join(SCRIPT_DIR, "profile", "hooks")
    if os.path.exists(hooks_src):
        for target_home in root_homes + root_profiles:
            target_dir = os.path.join(target_home, "hooks")
            os.makedirs(target_dir, exist_ok=True)
            for h_name in os.listdir(hooks_src):
                h_s = os.path.join(hooks_src, h_name)
                h_d = os.path.join(target_dir, h_name)
                if os.path.isdir(h_s):
                    if os.path.exists(h_d):
                        shutil.rmtree(h_d, ignore_errors=True)
                    shutil.copytree(h_s, h_d)

    # Sync career assets (CVs & Etihad certificates) to ~/.aethelgard/career_assets/
    assets_src = os.path.join(SCRIPT_DIR, "profile", "career_assets")
    if os.path.exists(assets_src):
        assets_dest = os.path.expanduser("~/.aethelgard/career_assets")
        if os.path.exists(assets_dest):
            shutil.rmtree(assets_dest, ignore_errors=True)
        shutil.copytree(assets_src, assets_dest)

    # Purge stale caches (remove any auto/best-coding leaks)
    for target_profile in root_profiles:
        for cache_f in ["provider_models_cache.json", "context_length_cache.yaml"]:
            cp = os.path.join(target_profile, cache_f)
            if os.path.exists(cp):
                try:
                    os.remove(cp)
                except Exception:
                    pass

    stt_script = os.path.join(SCRIPT_DIR, "transcribe_gemini.py")
    stt_cmd = f"python3 {stt_script} {{input_path}} --output_dir {{output_dir}}"

    # Write profile .env without deprecated TERMINAL_CWD (now set via terminal.cwd in config.yaml)
    for target_profile in root_profiles:
        with open(os.path.join(target_profile, ".env"), "w", encoding="utf-8") as f:
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

    for target_home in root_homes + root_profiles:
        _strip_deprecated_env_keys(os.path.join(target_home, ".env"))

    if GOOGLE_API_KEY:
        home_env = os.path.join(HERMES_HOME, ".env")
        try:
            os.makedirs(HERMES_HOME, exist_ok=True)
            existing = ""
            if os.path.exists(home_env):
                with open(home_env, "r", encoding="utf-8", errors="ignore") as hef_r:
                    existing = hef_r.read()
            if f"GOOGLE_API_KEY={GOOGLE_API_KEY}" not in existing:
                with open(home_env, "a", encoding="utf-8") as hef:
                    hef.write(f"\nGOOGLE_API_KEY={GOOGLE_API_KEY}\nGEMINI_API_KEY={GOOGLE_API_KEY}\n")
        except Exception:
            pass

    return root_homes, root_profiles, cfg_src, stt_cmd


def _export_runtime_env(stt_cmd: str = "") -> None:
    """Exports shared custodian runtime environment variables for background workers and Hermes."""
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
    if stt_cmd:
        os.environ["HERMES_LOCAL_STT_COMMAND"] = stt_cmd
    os.environ["AETHELGARD_DIR"] = SCRIPT_DIR
    os.environ["TERMINAL_CWD"] = SCRIPT_DIR
    os.environ["NODE_OPTIONS"] = "--dns-result-order=ipv4first --max-old-space-size=1536"


def init_hermes():
    print(f"\n⚙️ [3/4] Configuring Hermes Profile '{HERMES_PROFILE}'...")
    # Apply Hermes typing lifecycle hotfix & proxy hardening
    patch_results = patch_hermes_gateway_typing()
    print(f"⚡ [Hotfix 1] Applied Hermes Gateway typing lifecycle patches: {patch_results}", flush=True)

    _root_homes, root_profiles, cfg_src, stt_cmd = _sync_hermes_profile_files()
    _export_runtime_env(stt_cmd=stt_cmd)

    # Apply hermes CLI profile activation cleanly; skip 41 redundant `config set` CLI
    # invocations when authoritative `profile/config.yaml` is already synced.
    try:
        commands = [["hermes", "profile", "use", HERMES_PROFILE]]
        if not os.path.exists(cfg_src):
            commands.extend([
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
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "terminal.cwd", SCRIPT_DIR],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "slash_commands.confirm_new", "false"],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "slash_commands.confirm_reset", "false"],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "compression.enabled", "false"],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "stt.provider", "local_command"],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "agent.intent_ack_continuation", "true"],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "agent.tool_use_enforcement", "true"],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "agent.execution_guidance", "true"],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "agent.stall_guards", "true"],
                ["hermes", "-p", HERMES_PROFILE, "config", "set", "agent.api_max_retries", "2"],
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
            ])
        for cmd in commands:
            subprocess.run(cmd, check=False)

        # Authoritative sync of profile/config.yaml (pure Antigravity Pro configuration)
        if os.path.exists(cfg_src):
            for target_profile in root_profiles:
                _write_profile_config(cfg_src, os.path.join(target_profile, "config.yaml"), SCRIPT_DIR)
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
            _ensure_repo_remote_auth(SCRIPT_DIR)
            subprocess.run(["git", "-C", SCRIPT_DIR, "fetch", "origin", "main"], check=False, capture_output=True, timeout=15)
            local_rev = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
            remote_rev = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-parse", "origin/main"], capture_output=True, text=True, timeout=10).stdout.strip()
            if local_rev and remote_rev and local_rev != remote_rev:
                print(f"[Code Sync] 🚀 New commits detected on origin/main ({local_rev[:7]} -> {remote_rev[:7]})! Force-syncing and triggering reload...", flush=True)
                subprocess.run(["git", "-C", SCRIPT_DIR, "reset", "--hard", "origin/main"], check=False, timeout=15)
                with open(restart_path, "w", encoding="utf-8") as rf:
                    rf.write("code_update")
        except Exception:
            pass

        # 2. Vault Sync
        try:
            # First, pull any new commits from local desktop / GitHub
            _ensure_repo_remote_auth(VAULT_DIR)
            _abort_stale_git_rebase(VAULT_DIR)
            # Commit any local VM edits cleanly before pulling so git pull --rebase never stalls on unstaged files
            subprocess.run(["git", "-C", VAULT_DIR, "add", "."], check=False)
            pre_chk = subprocess.run(["git", "-C", VAULT_DIR, "commit", "-m", "chore: pre-sync vault state"], capture_output=True, text=True)
            pull_res = subprocess.run(["git", "-C", VAULT_DIR, "pull", "--rebase", "origin", VAULT_BRANCH], check=False)
            if pull_res.returncode != 0:
                _abort_stale_git_rebase(VAULT_DIR)
                subprocess.run(["git", "-C", VAULT_DIR, "pull", "--no-rebase", "--no-edit", "-X", "union", "origin", VAULT_BRANCH], check=False)
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
            f"🧠 Brain: {MODEL_DEFAULT} (Native Python Antigravity Gateway)\n"
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

def _restart_custodian_process():
    """Reboots the entire run.py process cleanly (never call sys.exit(0) inside a worker thread)."""
    try:
        import antigravity_proxy
        srv = getattr(antigravity_proxy, "_proxy_server_instance", None)
        if srv is not None:
            srv.shutdown()
            srv.server_close()
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    if os.name != "nt" and os.environ.get("INVOCATION_ID"):
        # Running under systemd (Restart=always): terminate the whole OS process so systemd reloads cleanly.
        os._exit(0)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _pull_repo_head(repo_dir: str):
    """Fetches and hard-resets repo_dir to origin/main using active remote auth."""
    _ensure_repo_remote_auth(repo_dir)
    subprocess.run(["git", "-C", repo_dir, "fetch", "origin", "main"], timeout=15, check=False)
    subprocess.run(["git", "-C", repo_dir, "reset", "--hard", "origin/main"], timeout=15, check=False)


def _apply_restart_trigger(restart_path: str, proc=None):
    """Consumes restart trigger, syncs repo & profile files, stops gateway child, and reboots."""
    try:
        os.remove(restart_path)
    except Exception:
        pass
    print("[Supervisor] 🔄 Emergency restart requested! Pulling latest code & recycling service...", flush=True)
    try:
        _pull_repo_head(SCRIPT_DIR)
    except Exception:
        pass
    unlock_stale_hermes_sessions()
    try:
        _sync_hermes_profile_files()
    except Exception:
        pass
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    unlock_stale_hermes_sessions()
    print("[Supervisor] 🚀 Restarting full custodian process for a clean 100% reload...", flush=True)
    _restart_custodian_process()


def emergency_reboot_worker():
    """Monitors running gateway for remote /restart triggers without killing turns."""
    restart_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(SCRIPT_DIR, "restart.trigger")

    omniroute_fail_count = 0
    while True:
        time.sleep(5)
        global active_gateway_proc
        proc = active_gateway_proc

        # 1. External Emergency Restart Trigger (from /restart, /reboot, or auto-updater)
        if os.path.exists(restart_path):
            _apply_restart_trigger(restart_path, proc=proc)

        if not proc or proc.poll() is not None:
            continue

        # 2. Antigravity proxy liveness check: restart thread if proxy went down (3 consecutive failures)
        if not is_omniroute_alive():
            omniroute_fail_count += 1
            if omniroute_fail_count >= 3:
                print("[Supervisor] 🚨 Antigravity proxy unresponsive (3 checks)! Restarting native proxy...", flush=True)
                start_omniroute_if_needed()
                omniroute_fail_count = 0
        else:
            omniroute_fail_count = 0


def test_antigravity_liveness(timeout_sec: int = 30) -> bool:
    """Sends a test chat completion request to local Antigravity Gateway to verify Antigravity Pro liveness."""
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
    1. Force-refreshes OAuth access token directly via antigravity_proxy.
    2. Ensures native Python Antigravity Gateway is listening on port 20128.
    3. Re-tests liveness; if still failing, triggers emergency service reload.
    """
    print("🚑 [Auto-Heal] Starting Antigravity auto-healing routine...", flush=True)

    # 1. Force-refresh OAuth token
    token_ok = False
    try:
        token = get_valid_access_token(force_refresh=True)
        token_ok = bool(token)
        print(f"[Auto-Heal] Token refresh result: {'SUCCESS' if token_ok else 'FAILED'}", flush=True)
    except Exception as te:
        print(f"[Auto-Heal] ⚠ Token refresh exception: {te}", flush=True)

    # 2. Ensure native proxy is running
    try:
        start_omniroute_if_needed()
    except Exception as oe:
        print(f"[Auto-Heal] ⚠ Proxy reload exception: {oe}", flush=True)

    # 3. Verify liveness post-heal
    time.sleep(2)
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


_BACKGROUND_WORKERS_STARTED = False
_BACKGROUND_WORKERS_LOCK = threading.Lock()


def _ensure_jarvis_tts_packages() -> None:
    """Auto-upgrades edge-tts>=7.3.1 (fixes Bing 403 Sec-MS-GEC) and ensures piper-tts backup."""
    import importlib
    import importlib.metadata

    def _ver_tuple(raw: str) -> tuple[int, ...]:
        nums = [int(p) for p in re.findall(r"\d+", raw or "")]
        return tuple(nums[:3]) if nums else (0,)

    try:
        edge_ver = importlib.metadata.version("edge-tts")
    except Exception:
        edge_ver = "0.0.0"

    if _ver_tuple(edge_ver) < (7, 3, 1):
        print(f"🎙️ Upgrading edge-tts ({edge_ver} -> >=7.3.1) for British JARVIS voice...", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "-q", "edge-tts>=7.3.1"],
            timeout=120,
            check=False,
        )
        if "edge_tts" in sys.modules:
            try:
                importlib.reload(sys.modules["edge_tts"])
            except Exception:
                pass

    try:
        importlib.metadata.version("piper-tts")
    except Exception:
        print("🎙️ Installing piper-tts British Male neural backup...", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "piper-tts>=1.3.0"],
            timeout=120,
            check=False,
        )


def _start_voice_and_scheduler_once() -> None:
    """Starts Proactive Scheduler, Telegram 1-on-1 Caller, and Live Web Bridge once per process."""
    global _BACKGROUND_WORKERS_STARTED
    with _BACKGROUND_WORKERS_LOCK:
        if _BACKGROUND_WORKERS_STARTED:
            return
        _BACKGROUND_WORKERS_STARTED = True

    _export_runtime_env()
    try:
        _ensure_jarvis_tts_packages()
    except Exception as tte:
        print(f"⚠ JARVIS TTS package check notice: {tte}", flush=True)

    # ── Launch Proactive Scheduler & Second Brain Librarian ──────────────
    try:
        from proactive_scheduler import proactive_scheduler_daemon
        threading.Thread(target=proactive_scheduler_daemon, args=(VAULT_DIR,), daemon=True).start()
        print("⏰ Proactive Shift Scheduler & Second Brain Librarian thread active!", flush=True)
    except Exception as se:
        print(f"⚠ Proactive Scheduler startup notice: {se}", flush=True)

    # ── Launch Native Telegram 1-on-1 Voice Caller (+20 MTProto Bridge) ──────
    try:
        try:
            import telethon  # noqa: F401
            import ntgcalls  # noqa: F401
            import qrcode  # noqa: F401
        except ImportError:
            print("📦 Installing telethon, ntgcalls & qrcode for Native Telegram Voice Caller...", flush=True)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "telethon", "ntgcalls", "qrcode"],
                timeout=120,
                check=False,
            )
        from telegram_voice_caller import start_telegram_caller_background
        start_telegram_caller_background()
        print("📱 Native Telegram 1-on-1 Voice Caller thread initialized (RPC :20132)!", flush=True)
    except Exception as tce:
        print(f"⚠ Telegram Voice Caller startup notice: {tce}", flush=True)

    # ── Pre-warm Offline British Male Neural Voice Backup & JARVIS Earcons ──
    try:
        import voice_engine
        voice_engine._kickoff_piper_prefetch_async(voice_engine.PIPER_VOICE_ENGLISH)
        threading.Thread(
            target=voice_engine.preload_jarvis_earcons,
            name="JarvisEarconPreload",
            daemon=True,
        ).start()
    except Exception as pfe:
        print(f"⚠ Piper backup prefetch notice: {pfe}", flush=True)

    # ── Recover Interrupted Persistent Missions & Start WhatsApp Baileys Sidecar ─
    try:
        import mission_engine
        recovered = mission_engine.recover_interrupted_missions(vault_dir=VAULT_DIR)
        if recovered:
            print(f"🛰️ Recovered {len(recovered)} persisted background mission(s): {recovered}", flush=True)
    except Exception as me:
        print(f"⚠ Mission recovery notice: {me}", flush=True)

    def _supervise_whatsapp_bridge() -> None:
        if os.environ.get("AETHELGARD_DISABLE_WA_BRIDGE") == "1":
            return
        import shutil
        node_bin = shutil.which("node")
        bridge_dir = Path(SCRIPT_DIR) / "whatsapp_bridge"
        server_js = bridge_dir / "server.js"
        if not node_bin or not server_js.exists():
            return
        if not (bridge_dir / "node_modules").exists():
            return
        while True:
            try:
                proc = subprocess.Popen(
                    [node_bin, str(server_js)],
                    cwd=str(bridge_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
                proc.wait()
            except Exception:
                pass
            time.sleep(8)

    try:
        threading.Thread(target=_supervise_whatsapp_bridge, name="WhatsAppBaileysBridge", daemon=True).start()
    except Exception:
        pass

    # ── Launch Live Voice Call Web Bridge ──────────────────────────────
    try:
        from live_call_server import start_live_call_server_background
        start_live_call_server_background()
        print("📞 Live Voice Call Web server active on port 20130!", flush=True)
    except Exception as ce:
        print(f"⚠ Live Call Server startup notice: {ce}", flush=True)


def run_gateway_forever():
    global active_gateway_proc, gateway_start_time
    print("\n" + "=" * 65)
    print("🚀 [4/4] LAUNCHING HERMES GATEWAY (24/7 ALWAYS-ON SUPERVISION)")
    print("=" * 65)

    threading.Thread(target=auto_sync_worker, daemon=True).start()
    threading.Thread(target=emergency_reboot_worker, daemon=True).start()
    threading.Thread(target=antigravity_health_check_worker, daemon=True, name="AntigravityHealthCheck").start()

    _start_voice_and_scheduler_once()

    send_telegram_startup()

    # Apply typing lifecycle patches and ensure PYTHONPATH includes SCRIPT_DIR
    patch_hermes_gateway_typing()
    cur_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_parts = cur_pythonpath.split(os.pathsep) if cur_pythonpath else []
    if SCRIPT_DIR not in pythonpath_parts:
        pythonpath_parts.insert(0, SCRIPT_DIR)
        os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    cmd = ["hermes", "-p", HERMES_PROFILE, "gateway", "run", "--replace", "--force", "--accept-hooks"]
    restart_path = "/tmp/hermes_restart_requested" if os.name != "nt" else os.path.join(SCRIPT_DIR, "restart.trigger")

    while True:
        try:
            if os.path.exists(restart_path):
                _apply_restart_trigger(restart_path)

            _sync_hermes_profile_files()
            unlock_stale_hermes_sessions()
            print(f"[{time.strftime('%H:%M:%S')}] Starting Hermes Gateway process...", flush=True)
            gateway_start_time = time.time()
            active_gateway_proc = subprocess.Popen(cmd, cwd=SCRIPT_DIR, env=dict(os.environ))
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
    auto_update_self(allow_reexec=True)
    init_vault()
    init_omniroute()
    _start_voice_and_scheduler_once()
    init_hermes()
    run_gateway_forever()
