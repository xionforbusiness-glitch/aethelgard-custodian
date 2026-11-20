#!/usr/bin/env python3
"""
🐙 Aethelgard Studio GitHub Deployer
===================================
Automates GitHub repository creation, local git staging, and force-push
under Omar's GitHub account (xionforbusiness-glitch).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from modules.studio.config import get_github_owner, get_github_token

logger = logging.getLogger("Aethelgard.GitHubDeployer")


class GitHubDeployer:
    """Creates and manages remote GitHub repositories for generated web applications."""

    def __init__(self, token: Optional[str] = None, owner: Optional[str] = None):
        self.token = token or get_github_token()
        self.owner = owner or get_github_owner()

    def create_or_verify_repo(self, repo_name: str, description: str = "", is_private: bool = False) -> Dict[str, Any]:
        """Ensures remote repository exists under owner's GitHub profile."""
        if not self.token:
            return {"success": False, "error": "No GitHub PAT configured in ~/.aethelgard/github_token or GITHUB_TOKEN"}

        clean_repo = repo_name.strip().lower().replace(" ", "-")
        api_url = f"https://api.github.com/repos/{self.owner}/{clean_repo}"
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Aethelgard-Studio-Swarm",
        }

        # 1. Check if repo already exists
        req = urllib.request.Request(api_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    logger.info(f"Remote repository already exists: {data.get('html_url')}")
                    return {"success": True, "repo_url": data.get("html_url"), "repo_name": clean_repo, "created": False}
        except urllib.error.HTTPError as e:
            if e.code != 404:
                logger.warning(f"GitHub API query error: {e.code} {e.reason}")

        # 2. Create new repository
        create_url = "https://api.github.com/user/repos"
        payload = {
            "name": clean_repo,
            "description": description or f"Built autonomously by Aethelgard Studio Swarm for {clean_repo}",
            "private": is_private,
            "auto_init": False,
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req_create = urllib.request.Request(create_url, data=data_bytes, headers={**headers, "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req_create, timeout=12) as resp:
                if resp.status in (200, 201):
                    res_data = json.loads(resp.read().decode("utf-8"))
                    html_url = res_data.get("html_url", f"https://github.com/{self.owner}/{clean_repo}")
                    logger.info(f"🎉 Remote GitHub repository created: {html_url}")
                    return {"success": True, "repo_url": html_url, "repo_name": clean_repo, "created": True}
                else:
                    return {"success": False, "error": f"Failed to create repo: HTTP {resp.status}"}
        except Exception as ce:
            logger.error(f"Failed to create repository on GitHub: {ce}")
            return {"success": False, "error": str(ce)}

    def push_project_directory(self, project_dir: Path, repo_name: str, commit_message: str = "") -> Dict[str, Any]:
        """Initializes git, stages all files, commits, and pushes to remote GitHub repository."""
        if not self.token:
            return {"success": False, "error": "No GitHub PAT configured"}

        clean_repo = repo_name.strip().lower().replace(" ", "-")
        auth_remote = f"https://x-access-token:{self.token}@github.com/{self.owner}/{clean_repo}.git"
        public_url = f"https://github.com/{self.owner}/{clean_repo}"

        try:
            cwd = str(project_dir.resolve())
            # Ensure .gitignore exists in project directory
            gitignore_path = project_dir / ".gitignore"
            if not gitignore_path.exists():
                gitignore_path.write_text("node_modules/\n.next/\nout/\n.env\n.env.local\n.DS_Store\n", encoding="utf-8")

            # Git Commands sequence
            subprocess.run(["git", "init"], cwd=cwd, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Aethelgard Studio Agent"], cwd=cwd, check=False)
            subprocess.run(["git", "config", "user.email", "xionforbusiness@gmail.com"], cwd=cwd, check=False)
            subprocess.run(["git", "checkout", "-B", "main"], cwd=cwd, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=cwd, check=True, capture_output=True)

            msg = commit_message or f"feat(release): autonomous build by Aethelgard Studio Swarm"
            subprocess.run(["git", "commit", "-m", msg], cwd=cwd, check=False, capture_output=True)

            # Check and configure remote origin
            check_remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd, capture_output=True, text=True)
            if check_remote.returncode == 0:
                subprocess.run(["git", "remote", "set-url", "origin", auth_remote], cwd=cwd, check=True)
            else:
                subprocess.run(["git", "remote", "add", "origin", auth_remote], cwd=cwd, check=True)

            # Push force to main
            push_res = subprocess.run(["git", "push", "-u", "origin", "main", "--force"], cwd=cwd, capture_output=True, text=True, timeout=30)
            if push_res.returncode == 0:
                logger.info(f"✅ Successfully pushed project to GitHub: {public_url}")
                return {"success": True, "repo_url": public_url, "pushed": True}
            else:
                logger.error(f"Git push failed: {push_res.stderr}")
                return {"success": False, "error": push_res.stderr, "repo_url": public_url}
        except Exception as pe:
            logger.error(f"Git execution exception: {pe}")
            return {"success": False, "error": str(pe), "repo_url": public_url}
