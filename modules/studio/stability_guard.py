#!/usr/bin/env python3
"""
🛡️ Aethelgard Studio Stability Guard
====================================
Context manager ensuring zero-interruption execution for multi-agent studio
tasks. Suppresses background auto-sync restarts and transient supervisor reloads
until code synthesis, Git push, Vercel deployment, and screenshot captures complete.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("Aethelgard.StudioStabilityGuard")

LOCK_FILE = "/tmp/aethelgard_studio_lock" if os.name != "nt" else str(Path(__file__).resolve().parent.parent.parent / "studio.lock")


def is_studio_locked() -> bool:
    """Checks whether an autonomous studio mission is currently holding the execution lock."""
    return os.path.exists(LOCK_FILE)


@contextlib.contextmanager
def studio_execution_lock(mission_name: str = "autonomous_studio_task"):
    """
    Acquires an execution lock preventing daemon auto-sync reloads during active builds.
    Guaranteed to release the lock in the finally block.
    """
    t0 = time.time()
    try:
        Path(LOCK_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(f"{mission_name}:{int(t0)}")
        logger.info(f"🔒 [StabilityGuard] Lock acquired for '{mission_name}'. Background reloads suppressed.")
        yield
    finally:
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
            elapsed = time.time() - t0
            logger.info(f"🔓 [StabilityGuard] Lock released for '{mission_name}' after {elapsed:.2f}s.")
        except Exception as e:
            logger.warning(f"[StabilityGuard] Error clearing lock file: {e}")
