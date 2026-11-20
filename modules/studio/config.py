#!/usr/bin/env python3
"""
⚙️ Aethelgard Studio Configuration & Credential Resolver
========================================================
Resolves credentials and environment settings for GitHub, Vercel, Figma, Canva,
and Telegram delivery across Linux VM and local Windows environments.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_env_val(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    candidates = [
        Path.home() / "aethelgard-custodian" / ".env",
        REPO_ROOT / ".env",
        Path.home() / ".hermes" / "profiles" / "llm-wiki" / ".env",
        Path.home() / ".aethelgard" / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == key:
                            cleaned = v.strip().strip("'\"")
                            if cleaned:
                                return cleaned
            except Exception:
                pass
    return default


def get_github_token() -> str:
    """Resolves Omar's active GitHub Personal Access Token."""
    token_file = Path.home() / ".aethelgard" / "github_token"
    if token_file.is_file():
        try:
            tok = token_file.read_text(encoding="utf-8").strip()
            if tok:
                return tok
        except Exception:
            pass
    return _load_env_val("GITHUB_TOKEN")


def get_github_owner() -> str:
    return _load_env_val("GITHUB_OWNER", "xionforbusiness-glitch")


def get_vercel_token() -> str:
    """Resolves Vercel API token from env or ~/.aethelgard/vercel_token."""
    token_file = Path.home() / ".aethelgard" / "vercel_token"
    if token_file.is_file():
        try:
            tok = token_file.read_text(encoding="utf-8").strip()
            if tok:
                return tok
        except Exception:
            pass
    return _load_env_val("VERCEL_TOKEN")


def get_figma_token() -> str:
    return _load_env_val("FIGMA_ACCESS_TOKEN") or _load_env_val("FIGMA_PAT")


def get_canva_key() -> str:
    return _load_env_val("CANVA_API_KEY") or _load_env_val("CANVA_CONNECT_KEY")


def get_telegram_bot_token() -> str:
    return _load_env_val("TELEGRAM_BOT_TOKEN")


def get_telegram_user_id() -> str:
    return _load_env_val("TELEGRAM_ALLOWED_USERS", "1021125594")


def get_studio_workspace_dir() -> Path:
    """Directory where projects are synthesized and built."""
    base = Path.home() / "aethelgard_studio_workspace"
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:
        fallback = REPO_ROOT / "staging" / "studio_workspace"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
