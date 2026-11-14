#!/usr/bin/env python3
"""
🪟 Aethelgard 1-Click Windows Account Pairing Helper (R4.2)

Captures and bundles Omar's session credentials on his Windows workstation and
syncs them to `~/.aethelgard/credentials/` (locally and/or to the 1GB Cloud VM):
  • Extracts session cookies from local Chrome/Edge SQLite databases (DPAPI / AES-GCM)
  • Bundles credentials with authenticated encryption (AES-256-GCM / HMAC-SHA256)
  • Supports both SSH/SCP sync and Zero-SSH encrypted Telegram/HTTP sync
  • Includes `--self-test` mode for programmatic verification
"""

from __future__ import annotations

import os
import sys
import hmac
import json
import time
import shutil
import base64
import hashlib
import sqlite3
import tempfile
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_KEY_HEX = os.environ.get(
    "STORAGE_ENCRYPTION_KEY",
    "0000000000000000000000000000000000000000000000000000000000000000",
)

TARGET_DOMAINS = (
    "linkedin.com",
    "facebook.com",
    "google.com",
    "whatsapp.com",
    "telegram.org",
)


def _derive_32byte_key(key_hex: Optional[str] = None) -> bytes:
    raw = (key_hex or DEFAULT_KEY_HEX).strip()
    try:
        if len(raw) == 64:
            return bytes.fromhex(raw)
    except ValueError:
        pass
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _xor_keystream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """Deterministic SHA-256 counter-mode stream cipher (zero native dependencies)."""
    out = bytearray(len(data))
    block_count = (len(data) + 31) // 32
    for counter in range(block_count):
        ctr_bytes = counter.to_bytes(8, "big")
        keystream_block = hashlib.sha256(key + nonce + ctr_bytes).digest()
        start = counter * 32
        end = min(start + 32, len(data))
        for i in range(start, end):
            out[i] = data[i] ^ keystream_block[i - start]
    return bytes(out)


def encrypt_credentials_bundle(payload: Dict[str, Any], key_hex: Optional[str] = None) -> bytes:
    """
    Encrypts a credentials dictionary into an authenticated binary bundle.
    Uses AES-256-GCM when `cryptography` is installed, with automatic fallback to
    authenticated SHA256-CTR + HMAC-SHA256 so it works on any Python environment.
    """
    key = _derive_32byte_key(key_hex)
    plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    nonce = os.urandom(12)

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, b"AETHELGARD_V1")
        envelope = {
            "v": 1,
            "alg": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ct": base64.b64encode(ciphertext).decode("ascii"),
        }
        return json.dumps(envelope).encode("utf-8")
    except ImportError:
        enc_key = hashlib.sha256(key + b":enc").digest()
        mac_key = hashlib.sha256(key + b":mac").digest()
        ciphertext = _xor_keystream(plaintext, enc_key, nonce)
        tag = hmac.new(mac_key, b"AETHELGARD_V1" + nonce + ciphertext, hashlib.sha256).digest()
        envelope = {
            "v": 1,
            "alg": "SHA256-CTR-HMAC",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ct": base64.b64encode(ciphertext).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        }
        return json.dumps(envelope).encode("utf-8")


def decrypt_credentials_bundle(bundle: bytes, key_hex: Optional[str] = None) -> Dict[str, Any]:
    """Decrypts and verifies an authenticated credentials bundle."""
    key = _derive_32byte_key(key_hex)
    try:
        envelope = json.loads(bundle.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Invalid credential bundle format: {e}") from e

    alg = envelope.get("alg")
    nonce = base64.b64decode(envelope["nonce"])
    ciphertext = base64.b64decode(envelope["ct"])

    if alg == "AES-256-GCM":
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, b"AETHELGARD_V1")
        return json.loads(plaintext.decode("utf-8"))

    if alg == "SHA256-CTR-HMAC":
        enc_key = hashlib.sha256(key + b":enc").digest()
        mac_key = hashlib.sha256(key + b":mac").digest()
        expected_tag = hmac.new(mac_key, b"AETHELGARD_V1" + nonce + ciphertext, hashlib.sha256).digest()
        provided_tag = base64.b64decode(envelope.get("tag", ""))
        if not hmac.compare_digest(expected_tag, provided_tag):
            raise ValueError("Bundle authentication tag verification failed")
        plaintext = _xor_keystream(ciphertext, enc_key, nonce)
        return json.loads(plaintext.decode("utf-8"))

    raise ValueError(f"Unsupported bundle encryption algorithm: {alg}")


def get_browser_cookie_db_candidates() -> List[str]:
    """
    Returns standard Windows Chrome, Edge, and Brave SQLite cookie database locations,
    scanning Default and Profile * subdirectories across each browser.
    """
    local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
    browser_roots = [
        os.path.join(local_app_data, "Google", "Chrome", "User Data"),
        os.path.join(local_app_data, "Microsoft", "Edge", "User Data"),
        os.path.join(local_app_data, "BraveSoftware", "Brave-Browser", "User Data"),
    ]
    candidates: List[str] = []
    for root in browser_roots:
        profiles = ["Default"]
        if os.path.isdir(root):
            try:
                discovered = sorted(
                    d for d in os.listdir(root)
                    if os.path.isdir(os.path.join(root, d))
                    and (d == "Default" or d.startswith("Profile "))
                )
                if discovered:
                    profiles = discovered
            except Exception:
                pass
        for prof in profiles:
            candidates.append(os.path.join(root, prof, "Network", "Cookies"))
            candidates.append(os.path.join(root, prof, "Cookies"))
    return candidates


def _dpapi_unprotect(encrypted_bytes: bytes) -> Optional[bytes]:
    """Decrypts DPAPI-protected bytes on Windows via CryptUnprotectData."""
    if os.name != "nt" or not encrypted_bytes:
        return None
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        blob_in = DATA_BLOB(
            len(encrypted_bytes),
            ctypes.cast(ctypes.create_string_buffer(encrypted_bytes, len(encrypted_bytes)), ctypes.POINTER(ctypes.c_char)),
        )
        blob_out = DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def _get_chromium_master_key(cookie_db_path: str) -> Optional[bytes]:
    """Loads and DPAPI-decrypts Chromium Local State os_crypt.encrypted_key."""
    cur_dir = Path(cookie_db_path).resolve().parent
    local_state_path: Optional[Path] = None
    for _ in range(4):
        candidate = cur_dir / "Local State"
        if candidate.is_file():
            local_state_path = candidate
            break
        cur_dir = cur_dir.parent
    if not local_state_path:
        return None
    try:
        data = json.loads(local_state_path.read_text(encoding="utf-8", errors="ignore"))
        enc_key_b64 = (data.get("os_crypt") or {}).get("encrypted_key", "")
        if not enc_key_b64:
            return None
        raw_key = base64.b64decode(enc_key_b64)
        if raw_key.startswith(b"DPAPI"):
            raw_key = raw_key[5:]
        return _dpapi_unprotect(raw_key)
    except Exception:
        return None


def _decrypt_windows_chrome_cookie(enc_val: bytes, master_key: Optional[bytes] = None) -> str:
    """
    Decrypts Chrome/Edge/Brave encrypted_value (v10/v11/v20 AES-256-GCM or legacy DPAPI),
    falling back gracefully to UTF-8 decoding for simulated test databases.
    """
    if not enc_val:
        return ""
    prefix = enc_val[:3]
    if prefix in (b"v10", b"v11", b"v20") and len(enc_val) > 15 and master_key:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

            nonce = enc_val[3:15]
            payload = enc_val[15:]
            plain = AESGCM(master_key).decrypt(nonce, payload, None)
            if prefix == b"v20" and len(plain) > 32:
                head_has_ctrl = any(b < 32 for b in plain[:32])
                tail_text = plain[32:].decode("utf-8", errors="strict")
                if head_has_ctrl and tail_text.isprintable():
                    return tail_text
            return plain.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    if prefix in (b"v10", b"v11", b"v20"):
        return ""

    dp = _dpapi_unprotect(enc_val)
    if dp is not None:
        try:
            decoded_dp = dp.decode("utf-8", errors="ignore")
            if decoded_dp:
                return decoded_dp
        except Exception:
            pass

    try:
        return enc_val.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def extract_browser_cookies(cookie_db_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Extracts LinkedIn, Facebook, Google, WhatsApp, and Telegram cookies from
    local Chrome/Edge/Brave SQLite databases. Gracefully returns empty dict if no DB exists.
    """
    candidates = cookie_db_paths if cookie_db_paths is not None else get_browser_cookie_db_candidates()
    existing_paths = [p for p in candidates if p and os.path.exists(p)]

    if not existing_paths:
        return {"cookies": {}, "platforms": {}, "found": False}

    extracted_cookies: Dict[str, Dict[str, str]] = {}

    for db_path in existing_paths:
        master_key = _get_chromium_master_key(db_path)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(tmp_fd)
        try:
            shutil.copy2(db_path, tmp_path)
            conn = sqlite3.connect(tmp_path, timeout=3)
            cur = conn.cursor()
            cur.execute("SELECT host_key, name, value, encrypted_value FROM cookies")
            for host_key, name, value, enc_val in cur.fetchall():
                host_str = str(host_key or "").lower()
                if not any(dom in host_str for dom in TARGET_DOMAINS):
                    continue
                cookie_val = str(value or "")
                if not cookie_val and isinstance(enc_val, (bytes, bytearray)):
                    cookie_val = _decrypt_windows_chrome_cookie(bytes(enc_val), master_key=master_key)
                if cookie_val:
                    extracted_cookies.setdefault(host_str, {})[str(name)] = cookie_val
            conn.close()
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    platforms: Dict[str, Dict[str, str]] = {}
    for host, cmap in extracted_cookies.items():
        if "linkedin.com" in host:
            platforms.setdefault("linkedin", {}).update(cmap)
        elif "facebook.com" in host:
            platforms.setdefault("facebook", {}).update(cmap)
        elif "google.com" in host:
            platforms.setdefault("google", {}).update(cmap)

    return {
        "cookies": extracted_cookies,
        "platforms": platforms,
        "found": bool(extracted_cookies),
    }


def save_credentials_locally(
    payload: Dict[str, Any],
    cred_dir: Optional[str] = None,
) -> Dict[str, str]:
    """
    Saves extracted or bundled credentials into `~/.aethelgard/credentials/`
    outside git-reset wipe paths.
    """
    target_dir = Path(cred_dir) if cred_dir else Path(
        os.environ.get("AETHELGARD_CREDENTIALS_DIR", os.path.expanduser("~/.aethelgard/credentials"))
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    written: Dict[str, str] = {}
    mapping = {
        "google": "google_oauth.json",
        "whatsapp": "whatsapp_session.json",
        "telegram": "telegram_session.json",
        "linkedin": "linkedin_session.json",
        "facebook": "facebook_session.json",
    }
    for key, filename in mapping.items():
        if key in payload and isinstance(payload[key], dict) and payload[key]:
            out_file = target_dir / filename
            out_file.write_text(json.dumps(payload[key], indent=2, ensure_ascii=False), encoding="utf-8")
            if os.name != "nt":
                try:
                    os.chmod(out_file, 0o600)
                except Exception:
                    pass
            written[key] = str(out_file)

    return written


def sync_bundle_to_vm(
    bundle_bytes: bytes,
    vm_host: Optional[str] = None,
    ssh_key: Optional[str] = None,
    send_via_telegram: bool = False,
) -> Dict[str, Any]:
    """
    Syncs the encrypted credentials bundle to the Cloud VM via:
      1. SSH/SCP (if vm_host is provided)
      2. Zero-SSH encrypted sync via Telegram Bot API
    """
    b64_bundle = base64.b64encode(bundle_bytes).decode("ascii")
    results: Dict[str, Any] = {"bundle_size_bytes": len(bundle_bytes), "ssh_synced": False, "zero_ssh_ready": True}

    if vm_host:
        with tempfile.NamedTemporaryFile(suffix=".enc", delete=False) as tf:
            tf.write(bundle_bytes)
            tmp_bundle = tf.name
        try:
            scp_cmd = ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
            if ssh_key:
                scp_cmd.extend(["-i", ssh_key])
            scp_cmd.extend([tmp_bundle, f"{vm_host}:~/.aethelgard/credentials/aethelgard_bundle.enc"])
            proc = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=15)
            results["ssh_synced"] = proc.returncode == 0
        except Exception as e:
            results["ssh_error"] = str(e)
        finally:
            if os.path.exists(tmp_bundle):
                os.remove(tmp_bundle)

    if send_via_telegram:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")
        if bot_token and chat_id:
            try:
                msg = (
                    "🔐 *Aethelgard Encrypted Account Bundle Generated*\n\n"
                    f"To import on any node without SSH, send:\n"
                    f"`/connect import {b64_bundle[:120]}...`"
                )
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    data=json.dumps({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=8) as r:
                    results["telegram_notified"] = r.status == 200
            except Exception:
                results["telegram_notified"] = False

    return results


def self_test() -> bool:
    """
    Runs a comprehensive, deterministic self-test verifying:
      1. Missing cookie database resilience
      2. Simulated SQLite cookie extraction for LinkedIn & Facebook
      3. Encrypted bundle round-trip & tamper detection
      4. Safe credential persistence in isolated directory
    """
    # 1. Test missing cookie DB handling
    empty_res = extract_browser_cookies(cookie_db_paths=["/nonexistent/path/Cookies.sqlite"])
    assert isinstance(empty_res, dict) and empty_res.get("cookies") == {}, "Missing DB fallback failed"

    # 2. Test simulated SQLite cookie DB extraction
    with tempfile.TemporaryDirectory() as tmp_dir:
        mock_db = os.path.join(tmp_dir, "Cookies")
        conn = sqlite3.connect(mock_db)
        cur = conn.cursor()
        cur.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)")
        cur.execute("INSERT INTO cookies VALUES ('.linkedin.com', 'li_at', 'mock_li_at_jwt_123', X'')")
        cur.execute("INSERT INTO cookies VALUES ('.facebook.com', 'c_user', '1000998877', X'')")
        cur.execute("INSERT INTO cookies VALUES ('.facebook.com', 'xs', 'mock_xs_secret', X'')")
        conn.commit()
        conn.close()

        extracted = extract_browser_cookies(cookie_db_paths=[mock_db])
        assert extracted.get("found") is True, "Simulated cookie DB extraction failed"
        assert extracted["platforms"]["linkedin"]["li_at"] == "mock_li_at_jwt_123"
        assert extracted["platforms"]["facebook"]["c_user"] == "1000998877"

        # 3. Test encryption & decryption round-trip
        sample_payload = {
            "google": {"access_token": "ya29.self_test", "refresh_token": "1//self_test"},
            "whatsapp": {"connected": True, "phone": "+963912345678"},
            "telegram": {"connected": True, "username": "@omar_elnemr"},
            "linkedin": extracted["platforms"]["linkedin"],
            "facebook": extracted["platforms"]["facebook"],
        }
        bundle = encrypt_credentials_bundle(sample_payload)
        decrypted = decrypt_credentials_bundle(bundle)
        assert decrypted == sample_payload, "Decrypted payload mismatch"

        # 4. Test local credential persistence
        cred_sub = os.path.join(tmp_dir, "creds")
        saved = save_credentials_locally(decrypted, cred_dir=cred_sub)
        assert len(saved) == 5, f"Expected 5 saved platform files, got {len(saved)}"
        assert os.path.exists(os.path.join(cred_sub, "google_oauth.json"))
        assert os.path.exists(os.path.join(cred_sub, "linkedin_session.json"))

    return True


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard 1-Click Windows Account Pairing Helper")
    parser.add_argument("--self-test", action="store_true", help="Run programmatic self-test suite")
    parser.add_argument("--extract", action="store_true", help="Extract browser cookies and save locally")
    parser.add_argument("--vm-host", default=None, help="Optional SSH user@host for Cloud VM sync")
    parser.add_argument("--ssh-key", default=None, help="Optional SSH private key path")
    args = parser.parse_args()

    if args.self_test:
        ok = self_test()
        print(json.dumps({"self_test": "PASSED" if ok else "FAILED", "success": ok}, indent=2))
        return 0 if ok else 1

    print("🏛️ Aethelgard 1-Click Windows Account Pairing Helper")
    extracted = extract_browser_cookies()
    payload = extracted.get("platforms", {})
    saved = save_credentials_locally(payload)
    bundle = encrypt_credentials_bundle(payload)
    bundle_file = Path(os.path.expanduser("~/.aethelgard/credentials/aethelgard_bundle.enc"))
    bundle_file.parent.mkdir(parents=True, exist_ok=True)
    bundle_file.write_bytes(bundle)

    sync_res = sync_bundle_to_vm(bundle, vm_host=args.vm_host, ssh_key=args.ssh_key)
    print(json.dumps({
        "cookies_found": extracted.get("found", False),
        "saved_files": saved,
        "encrypted_bundle": str(bundle_file),
        "sync": sync_res,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
