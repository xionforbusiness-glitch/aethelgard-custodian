#!/usr/bin/env python3
"""
🛡️ Aethelgard Antigravity OAuth Token Maintainer
Guarantees OmniRoute's Google Antigravity OAuth token is perpetually valid.
Runs proactively every 20 minutes (before the 60-minute Google token expiration).
"""

import os
import sys
import json
import time
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

REFRESH_TOKEN = os.environ.get(
    "ANTIGRAVITY_REFRESH_TOKEN",
    "1//09LVvmEt9o7BzCgYIARAAGAkSNgF-L9IrTGV5HzlX1Aun-eVnScYhZG1D21hHUC5cdLlgsduEIQL2LJEIBDw8TEIenG_RqnI8Rw"
)
CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-REDACTED_CLIENT_SECRET"
STORAGE_ENCRYPTION_KEY = os.environ.get(
    "STORAGE_ENCRYPTION_KEY",
    "0000000000000000000000000000000000000000000000000000000000000000"
)
STATIC_SALT = b"omniroute-field-encryption-v1"


def fetch_fresh_google_token() -> tuple[str | None, int]:
    """Calls Google's OAuth endpoint to exchange refresh_token for a fresh access_token."""
    url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
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


def sync_token_to_sqlite(db_path: str) -> bool:
    """Refreshes the token and writes it directly to OmniRoute's storage.sqlite."""
    if not os.path.exists(db_path):
        return False

    token, expires_in = fetch_fresh_google_token()
    if not token:
        return False

    enc_token = encrypt_for_omniroute(token)
    now_utc = datetime.now(timezone.utc)
    expires_at = (now_utc + timedelta(seconds=expires_in)).isoformat().replace("+00:00", "Z")
    updated_at = now_utc.isoformat().replace("+00:00", "Z")

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cur = conn.cursor()
        cur.execute("""
            UPDATE provider_connections
            SET access_token = ?,
                expires_at = ?,
                token_expires_at = ?,
                test_status = 'active',
                last_error = NULL,
                last_error_at = NULL,
                last_error_type = NULL,
                last_error_source = NULL,
                error_code = NULL,
                updated_at = ?
            WHERE provider = 'antigravity'
        """, (enc_token, expires_at, expires_at, updated_at))
        rows = cur.rowcount
        conn.commit()
        conn.close()
        if rows > 0:
            print(f"[Token Maintainer] ✅ OmniRoute Antigravity token refreshed in {os.path.basename(db_path)} (valid for {expires_in}s)", flush=True)
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
    if len(sys.argv) > 1:
        target_db = sys.argv[1]
    else:
        target_db = os.path.expanduser("~/.omniroute/storage.sqlite")
    ok = sync_token_to_sqlite(target_db)
    print("Execution result:", ok)
