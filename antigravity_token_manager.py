#!/usr/bin/env python3
"""
🛡️ Aethelgard Antigravity OAuth Token & Live Model Catalog Maintainer
1. Guarantees OmniRoute's Google Antigravity OAuth token is perpetually valid (refreshed every 20m).
2. Dynamically queries Google Antigravity's v1internal:fetchAvailableModels catalog.
3. Hot-patches OmniRoute's Antigravity catalog & Claude 5.5 effort-variant handling (v3.8.52 fixes)
   so gemini-3.8-flash-{low,medium,high} and claude-sonnet-5-5-{low,medium,high} work with 0 errors.
4. Clears any transient 503/429 backoff or circuit-breaker lockouts in OmniRoute's storage.sqlite.
"""

import os
import re
import sys
import json
import time
import shutil
import hashlib
import sqlite3
import threading
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# Ensure UTF-8 stdout/stderr on all terminals
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def _load_secret_or_env(key_name: str, creds_field: str = "") -> str:
    """Safely loads credential from os.environ, local .env, or ~/.aethelgard/credentials/."""
    val = os.environ.get(key_name, "").strip()
    if val:
        return val
    # Try local .env
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as ef:
                for line in ef:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == key_name:
                            return v.strip().strip("'\"")
        except Exception:
            pass
    # Try ~/.aethelgard/credentials/antigravity.json
    creds_file = os.path.expanduser("~/.aethelgard/credentials/antigravity.json")
    if os.path.isfile(creds_file):
        try:
            with open(creds_file, "r", encoding="utf-8") as cf:
                data = json.load(cf)
                if creds_field and creds_field in data:
                    return str(data[creds_field]).strip()
                if key_name.lower() in data:
                    return str(data[key_name.lower()]).strip()
        except Exception:
            pass
    return ""

REFRESH_TOKEN = _load_secret_or_env("ANTIGRAVITY_REFRESH_TOKEN", "refresh_token")
CLIENT_ID = os.environ.get("ANTIGRAVITY_CLIENT_ID", "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com")
CLIENT_SECRET = _load_secret_or_env("ANTIGRAVITY_CLIENT_SECRET", "client_secret")
STORAGE_ENCRYPTION_KEY = _load_secret_or_env("STORAGE_ENCRYPTION_KEY", "storage_encryption_key")
STATIC_SALT = b"omniroute-field-encryption-v1"

# Built-in forward map from retired Antigravity models to current live Antigravity models
DEPRECATED_MODEL_FORWARD_MAP = {
    "gemini-3.7-flash": "gemini-3.8-flash-low",
    "gemini-3.7-flash-low": "gemini-3.8-flash-low",
    "gemini-3.7-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.7-flash-high": "gemini-3.8-flash-high",
    "gemini-3.7-flash-tiered": "gemini-3.8-flash-tiered",
    "antigravity/gemini-3.7-flash": "antigravity/gemini-3.8-flash-low",
    "antigravity/gemini-3.7-flash-low": "antigravity/gemini-3.8-flash-low",
    "antigravity/gemini-3.7-flash-medium": "antigravity/gemini-3.8-flash-medium",
    "antigravity/gemini-3.7-flash-high": "antigravity/gemini-3.8-flash-high",
    "antigravity/gemini-3.7-flash-tiered": "antigravity/gemini-3.8-flash-tiered",
}


def fetch_fresh_google_token(refresh_token: str | None = None) -> tuple[str | None, int]:
    """Calls Google's OAuth endpoint to exchange refresh_token for a fresh access_token."""
    active_refresh = refresh_token or REFRESH_TOKEN
    url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": active_refresh,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            access_token = data.get("access_token")
            expires_in = int(data.get("expires_in", 3600))
            return access_token, expires_in
    except Exception as e:
        print(f"[Token Maintainer] ⚠ Google OAuth refresh failed: {e}", flush=True)
        return None, 0


def discover_live_antigravity_models(access_token: str | None = None) -> list[str]:
    """Queries daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels for live model IDs."""
    token = access_token
    if not token:
        token, _ = fetch_fresh_google_token()
    if not token:
        return []

    endpoints = [
        "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
    ]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "antigravity/ide/2.21.1 darwin/arm64"
    }
    for url in endpoints:
        try:
            req = urllib.request.Request(url, data=b"{}", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = list(data.get("models", {}).keys())
                if models:
                    return models
        except Exception:
            continue
    return []


def find_omniroute_root() -> str | None:
    """Locates the installed omniroute npm package directory across Linux and Windows."""
    candidates = [
        os.path.expanduser("~/AppData/Roaming/npm/node_modules/omniroute"),
        "/usr/local/lib/node_modules/omniroute",
        "/usr/lib/node_modules/omniroute",
        os.path.expanduser("~/.npm-global/lib/node_modules/omniroute"),
    ]
    # Check nvm directories if present
    nvm_dir = os.path.expanduser("~/.nvm/versions/node")
    if os.path.exists(nvm_dir):
        try:
            for ver in os.listdir(nvm_dir):
                candidates.append(os.path.join(nvm_dir, ver, "lib", "node_modules", "omniroute"))
        except Exception:
            pass

    which_omni = shutil.which("omniroute")
    if which_omni:
        real_bin = os.path.realpath(which_omni)
        pkg_dir = os.path.dirname(os.path.dirname(real_bin))
        if os.path.exists(os.path.join(pkg_dir, "package.json")):
            candidates.insert(0, pkg_dir)

    for c in candidates:
        if c and os.path.exists(os.path.join(c, "package.json")):
            return c
    return None


def patch_omniroute_antigravity_catalog() -> bool:
    """
    Applies the OmniRoute v3.8.52 Antigravity fixes to the installed OmniRoute package:
    1. Remaps retired gemini-3.7-flash-tiered -> gemini-3.8-flash-tiered so legacy aliases work.
    2. Prevents applyClaudeEffortVariant from stripping -low/-medium/-high off Antigravity Claude 5.5 models.
    """
    omni_root = find_omniroute_root()
    if not omni_root:
        return False

    patched_files = 0
    target_subdirs = [
        os.path.join(omni_root, "src"),
        os.path.join(omni_root, "open-sse"),
        os.path.join(omni_root, "dist", ".build", "next", "server", "chunks"),
    ]
    for sub in target_subdirs:
        if not os.path.exists(sub):
            continue
        for root, dirs, files in os.walk(sub):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git")]
            for fname in files:
                if not fname.endswith((".js", ".mjs", ".ts")):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()

                    original = content

                    # 1. Forward retired gemini-3.7-flash-tiered target in ANTIGRAVITY_MODEL_ALIASES to gemini-3.8-flash-tiered
                    if "gemini-3.7-flash-tiered" in content:
                        content = content.replace('"gemini-3.7-flash-tiered"', '"gemini-3.8-flash-tiered"')

                    # 2. Keep Claude 5.5 tiered IDs literal when provider is antigravity/agy (OmniRoute PR #15566)
                    old_claude_cond = "if (effort && (isDirectClaudeLane || isKnownClaudeEffortBaseModel(baseModel)))"
                    new_claude_cond = 'if (effort && provider !== "antigravity" && provider !== "agy" && (isDirectClaudeLane || isKnownClaudeEffortBaseModel(baseModel)))'
                    if old_claude_cond in content:
                        content = content.replace(old_claude_cond, new_claude_cond)

                    # Also patch compiled Next.js server chunks where variables are minified
                    old_minified_claude = "if(d&&(p||(0,s.isKnownClaudeEffortBaseModel)(e)))"
                    new_minified_claude = 'if(d&&"antigravity"!==t&&"agy"!==t&&(p||(0,s.isKnownClaudeEffortBaseModel)(e)))'
                    if old_minified_claude in content:
                        content = content.replace(old_minified_claude, new_minified_claude)

                    if content != original:
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.write(content)
                        patched_files += 1
                except Exception:
                    pass

    if patched_files > 0:
        print(f"[Token Maintainer] 🔧 Hot-patched {patched_files} OmniRoute file(s) with Gemini 3.8 Flash & Claude 5.5 support.", flush=True)
    return True


def encrypt_for_omniroute(plaintext: str) -> str:
    """Encrypts plaintext string into OmniRoute's enc:v1:<iv>:<ct>:<tag> format."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = hashlib.scrypt(
            STORAGE_ENCRYPTION_KEY.encode(),
            salt=STATIC_SALT,
            n=16384,
            r=8,
            p=1,
            maxmem=0,
            dklen=32
        )
        aesgcm = AESGCM(key)
        iv = os.urandom(16)
        ct_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
        ct = ct_with_tag[:-16]
        tag = ct_with_tag[-16:]
        return f"enc:v1:{iv.hex()}:{ct.hex()}:{tag.hex()}"
    except Exception:
        # Fallback to inline Node.js if cryptography is not present
        script = f"""
        const {{ createCipheriv, randomBytes, scryptSync }} = require('crypto');
        const key = scryptSync('{STORAGE_ENCRYPTION_KEY}', '{STATIC_SALT.decode()}', 32);
        const iv = randomBytes(16);
        const c = createCipheriv('aes-256-gcm', key, iv);
        let ct = c.update({json.dumps(plaintext)}, 'utf8', 'hex') + c.final('hex');
        const tag = c.getAuthTag().toString('hex');
        console.log(`enc:v1:${{iv.toString('hex')}}:${{ct}}:${{tag}}`);
        """
        res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return res.stdout.strip()


def sync_token_to_sqlite(db_path: str, refresh_token: str | None = None) -> bool:
    """
    Refreshes the token, clears any provider backoff/lockout state, configures deprecation aliases,
    and writes both access_token and refresh_token (encrypted) directly to OmniRoute's storage.sqlite.
    """
    if not os.path.exists(db_path):
        return False

    active_refresh = refresh_token or REFRESH_TOKEN
    token, expires_in = fetch_fresh_google_token(active_refresh)
    if not token:
        return False

    enc_access_token = encrypt_for_omniroute(token)
    enc_refresh_token = encrypt_for_omniroute(active_refresh)
    now_utc = datetime.now(timezone.utc)
    expires_at = (now_utc + timedelta(seconds=expires_in)).isoformat().replace("+00:00", "Z")
    updated_at = now_utc.isoformat().replace("+00:00", "Z")

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cur = conn.cursor()
        cur.execute("""
            UPDATE provider_connections
            SET access_token = ?,
                refresh_token = ?,
                expires_at = ?,
                token_expires_at = ?,
                default_model = 'gemini-3.8-flash-low',
                test_status = 'active',
                backoff_level = 0,
                rate_limited_until = NULL,
                last_error = NULL,
                last_error_at = NULL,
                last_error_type = NULL,
                last_error_source = NULL,
                error_code = NULL,
                updated_at = ?
            WHERE provider = 'antigravity'
        """, (enc_access_token, enc_refresh_token, expires_at, expires_at, updated_at))
        rows = cur.rowcount

        # Clear any transient domain lockouts or circuit breakers so 503 spikes never lock out Antigravity
        for lock_table in ("domain_lockout_state", "domain_circuit_breakers", "connection_runtime_state"):
            try:
                cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{lock_table}';")
                if cur.fetchone():
                    cur.execute(f"DELETE FROM {lock_table};")
            except Exception:
                pass

        # Persist custom model deprecation aliases in key_value table
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='key_value';")
            if cur.fetchone():
                cur.execute(
                    "INSERT OR REPLACE INTO key_value (namespace, key, value) VALUES (?, ?, ?)",
                    ("settings", "customAliases", json.dumps(DEPRECATED_MODEL_FORWARD_MAP))
                )
        except Exception:
            pass

        conn.commit()
        conn.close()
        if rows > 0:
            print(f"[Token Maintainer] ✅ OmniRoute Antigravity access & refresh tokens refreshed in {os.path.basename(db_path)} (valid for {expires_in}s)", flush=True)
            return True
        else:
            return False
    except Exception as dbe:
        print(f"[Token Maintainer] ⚠ DB update failed: {dbe}", flush=True)
        return False


def start_token_maintainer_thread(db_path: str, interval_sec: int = 1200):
    """Launches a background daemon that proactively refreshes Antigravity OAuth tokens every 20 minutes."""
    def _worker():
        while True:
            try:
                sync_token_to_sqlite(db_path)
            except Exception as e:
                print(f"[Token Maintainer Notice] {e}", flush=True)
            time.sleep(interval_sec)

    t = threading.Thread(target=_worker, daemon=True, name="AntigravityTokenMaintainer")
    t.start()
    return t


if __name__ == "__main__":
    patch_omniroute_antigravity_catalog()
    if len(sys.argv) > 1:
        target_db = sys.argv[1]
    else:
        target_db = os.path.expanduser("~/.omniroute/storage.sqlite")
    ok = sync_token_to_sqlite(target_db)
    print("Execution result:", ok)
