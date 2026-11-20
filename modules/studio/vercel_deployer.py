#!/usr/bin/env python3
"""
⚡ Aethelgard Studio Vercel Deployer
===================================
Automates one-command production deployments to Vercel via Vercel CLI (or REST API),
returning instant HTTPS production URLs (*.vercel.app).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from modules.studio.config import get_vercel_token

logger = logging.getLogger("Aethelgard.VercelDeployer")


class VercelDeployer:
    """Deploys Next.js / React projects to Vercel production."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or get_vercel_token()

    def deploy_project(self, project_dir: Path, project_name: str) -> Dict[str, Any]:
        """Runs headless production deployment and parses live URL."""
        clean_name = project_name.strip().lower().replace(" ", "-")

        if not self.token:
            logger.warning("[VercelDeployer] VERCEL_TOKEN not configured. Returning staged preview deployment info.")
            return {
                "success": False,
                "needs_auth": True,
                "project_name": clean_name,
                "error": "VERCEL_TOKEN is not configured in .env or ~/.aethelgard/vercel_token",
                "message": (
                    "To enable instant live Vercel deployments, generate a token at "
                    "https://vercel.com/account/tokens and add VERCEL_TOKEN to your environment."
                ),
            }

        cmd = [
            "npx",
            "vercel",
            "deploy",
            "--prod",
            "--yes",
            "--name",
            clean_name,
            "--token",
            self.token,
        ]

        logger.info(f"🚀 Deploying project '{clean_name}' to Vercel production...")
        try:
            cwd = str(project_dir.resolve())
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            combined = f"{stdout}\n{stderr}"

            # Regex search for https://*.vercel.app
            urls = re.findall(r"https://[a-zA-Z0-9\-_\.]+\.vercel\.app", combined)
            if urls:
                live_url = urls[-1]  # The production deployment URL is printed at the end
                logger.info(f"🎉 Live Vercel production URL: {live_url}")

                # Optional verification check
                is_live = self._verify_url_liveness(live_url)

                return {
                    "success": True,
                    "live_url": live_url,
                    "verified": is_live,
                    "raw_output": stdout[-300:],
                }
            else:
                logger.warning(f"Vercel CLI completed but no *.vercel.app URL found. Stdout: {stdout[:200]}")
                return {
                    "success": False,
                    "error": "Could not extract production URL from Vercel CLI output.",
                    "raw_output": combined[-500:],
                }
        except subprocess.TimeoutExpired:
            logger.error("Vercel deployment timed out after 120s.")
            return {"success": False, "error": "Vercel deployment timed out (120s limit)."}
        except Exception as e:
            logger.error(f"Vercel deployment exception: {e}")
            return {"success": False, "error": str(e)}

    def _verify_url_liveness(self, url: str, retries: int = 4, delay: float = 3.0) -> bool:
        """Polls production URL until it returns HTTP 200."""
        req = urllib.request.Request(url, headers={"User-Agent": "Aethelgard-QA-Verifier"})
        for i in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status in (200, 301, 302, 308):
                        return True
            except Exception:
                time.sleep(delay)
        return False
