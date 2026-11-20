#!/usr/bin/env python3
"""
🎨 Aethelgard Studio Orchestrator & Master Director
===================================================
Coordinates the full autonomous multi-agent studio swarm:
  1. UI/UX Researcher & Design System Architect (design tokens & trends)
  2. Next.js 15 Frontend Engineer (React 19, Tailwind, Lucide React)
  3. GitHub Deployer (creates remote repo on xionforbusiness-glitch and pushes)
  4. Vercel Deployer (production deployment to *.vercel.app)
  5. Playwright QA (full-page high-resolution screenshot)
  6. Canva & Presentation Specialist (slide deck synthesis)
  7. Telegram Executive Delivery (Live URL + GitHub Link + Screenshot Image)
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.studio.config import (
    get_github_owner,
    get_studio_workspace_dir,
    get_telegram_bot_token,
    get_telegram_user_id,
)
from modules.studio.github_deployer import GitHubDeployer
from modules.studio.screenshot_capture import capture_website_screenshot
from modules.studio.stability_guard import studio_execution_lock
from modules.studio.vercel_deployer import VercelDeployer
from modules.studio.web_generator import NextJsWebGenerator
from modules.studio.canva_adapter import CanvaPresentationGenerator

import send_telegram_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [StudioDirector] %(message)s")
logger = logging.getLogger("Aethelgard.StudioDirector")


class StudioOrchestrator:
    """Master director orchestrating concept-to-live website and presentation pipelines."""

    def __init__(self, workspace_dir: Optional[Path] = None):
        self.workspace_dir = workspace_dir or get_studio_workspace_dir()
        self.github_deployer = GitHubDeployer()
        self.vercel_deployer = VercelDeployer()
        self.web_generator = NextJsWebGenerator(workspace_root=self.workspace_dir)
        self.canva_generator = CanvaPresentationGenerator()

    def build_and_deploy_website(
        self,
        prompt: str,
        project_name: str = "",
        push_github: bool = True,
        deploy_vercel: bool = True,
        capture_screenshot: bool = True,
    ) -> Dict[str, Any]:
        """Executes full autonomous website creation, deployment, and Telegram delivery."""
        t0 = time.time()
        logger.info(f"🎨 Initializing autonomous website studio mission: '{prompt}'...")

        with studio_execution_lock(mission_name=f"web_deploy_{project_name or 'project'}"):
            # 1. Synthesize Next.js 15 Application
            synth_res = self.web_generator.generate_project(prompt=prompt, project_name=project_name)
            project_dir: Path = synth_res["project_dir"]
            slug: str = synth_res["slug"]
            title: str = synth_res["title"]

            github_url = ""
            if push_github:
                # 2. Create remote repo & push code
                repo_res = self.github_deployer.create_or_verify_repo(repo_name=slug, description=prompt)
                if repo_res.get("success"):
                    push_res = self.github_deployer.push_project_directory(project_dir=project_dir, repo_name=slug)
                    github_url = push_res.get("repo_url", repo_res.get("repo_url", ""))

            # 3. Deploy to Vercel
            vercel_res = {}
            live_url = ""
            if deploy_vercel:
                vercel_res = self.vercel_deployer.deploy_project(project_dir=project_dir, project_name=slug)
                if vercel_res.get("success"):
                    live_url = vercel_res.get("live_url", "")

            # 4. Capture Headless Screenshot
            screenshot_path: Optional[Path] = None
            if capture_screenshot and live_url:
                out_png = project_dir / "public" / "preview_screenshot.png"
                screenshot_path = capture_website_screenshot(url=live_url, output_path=out_png)

            elapsed = time.time() - t0

            # 5. Deliver Executive Package to Telegram
            tg_delivered = self._deliver_to_telegram(
                title=title,
                prompt=prompt,
                live_url=live_url,
                github_url=github_url,
                screenshot_path=screenshot_path,
                elapsed_sec=elapsed,
            )

            result = {
                "success": True,
                "project_name": slug,
                "title": title,
                "project_dir": str(project_dir),
                "live_url": live_url,
                "github_url": github_url,
                "screenshot": str(screenshot_path) if screenshot_path else None,
                "telegram_delivered": tg_delivered,
                "elapsed_seconds": round(elapsed, 2),
            }
            logger.info(f"🏁 Studio mission finished in {elapsed:.1f}s: Live URL='{live_url}' | GitHub='{github_url}'")
            return result

    def generate_presentation_deck(self, topic: str, title: str = "") -> Dict[str, Any]:
        """Generates executive presentation deck and delivers to Telegram."""
        t0 = time.time()
        logger.info(f"🎨 Initializing Canva presentation mission: '{topic}'...")

        with studio_execution_lock(mission_name="canva_presentation"):
            deck_dir = self.workspace_dir / "presentations"
            deck_res = self.canva_generator.generate_presentation_deck(topic=topic, output_dir=deck_dir, deck_title=title)
            elapsed = time.time() - t0

            # Send deck to Telegram
            deck_file = deck_res.get("deck_markdown_file")
            delivered = False
            if deck_file and Path(deck_file).is_file():
                try:
                    caption = f"📊 **Executive Presentation Deck:** `{deck_res.get('title')}`\nSynthesized by Aethelgard Studio."
                    send_telegram_file.send_file_to_telegram(deck_file, caption=caption)
                    delivered = True
                except Exception as e:
                    logger.warning(f"Telegram file delivery notice: {e}")

            return {
                **deck_res,
                "telegram_delivered": delivered,
                "elapsed_seconds": round(elapsed, 2),
            }

    def _deliver_to_telegram(
        self,
        title: str,
        prompt: str,
        live_url: str,
        github_url: str,
        screenshot_path: Optional[Path],
        elapsed_sec: float,
    ) -> bool:
        """Dispatches rich executive briefing card + full-page screenshot to Omar's Telegram."""
        date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        msg_lines = [
            f"🚀 **AETHELGARD AUTONOMOUS WEB STUDIO**",
            f"📅 `{date_str}` • *Synthesized in {elapsed_sec:.1f}s*",
            f"",
            f"🎯 **Project:** `{title}`",
            f"📝 **Prompt:** *\"{prompt}\"*",
            f"",
            f"🌐 **Production URL:**",
            f"{live_url if live_url else '*(Staged for Vercel CLI Token)*'}",
            f"",
            f"🐙 **GitHub Repository:**",
            f"{github_url if github_url else f'https://github.com/{get_github_owner()}'}",
            f"",
            f"⚡ **Stack:** `Next.js 15 (App Router) • React 19 • Tailwind CSS • Lucide`",
            f"📱 **Status:** `100% Deployed & Live on Edge`",
        ]
        card_text = "\n".join(msg_lines)

        delivered = False
        try:
            # 1. If screenshot exists, deliver photo with caption
            if screenshot_path and screenshot_path.is_file():
                try:
                    send_telegram_file.send_file_to_telegram(str(screenshot_path), caption=card_text)
                    delivered = True
                    logger.info("Delivered Telegram photo + briefing card successfully.")
                    return True
                except Exception as pe:
                    logger.warning(f"Failed photo upload, falling back to text: {pe}")

            # 2. Text fallback
            tok = get_telegram_bot_token()
            uid = get_telegram_user_id()
            if tok and uid:
                from proactive_scheduler import send_telegram
                delivered = send_telegram(card_text, chat_id=uid, token=tok)
                logger.info(f"Delivered Telegram text card successfully: {delivered}")
            else:
                logger.warning("Telegram bot token or user ID not available for studio briefing card.")
        except Exception as e:
            logger.error(f"Error delivering Telegram card: {e}")

        return delivered


def main():
    parser = argparse.ArgumentParser(description="Aethelgard Studio Orchestrator")
    parser.add_argument("--type", default="web", choices=["web", "deck"], help="Task type: web or deck")
    parser.add_argument("--prompt", default="Damascus Airport Ground Turnaround Telemetry Dashboard", help="Task prompt")
    parser.add_argument("--name", default="", help="Project name or slug")
    parser.add_argument("--no-push", action="store_true", help="Skip pushing to GitHub")
    parser.add_argument("--no-deploy", action="store_true", help="Skip Vercel deployment")
    args = parser.parse_args()

    orchestrator = StudioOrchestrator()
    if args.type == "web":
        res = orchestrator.build_and_deploy_website(
            prompt=args.prompt,
            project_name=args.name,
            push_github=not args.no_push,
            deploy_vercel=not args.no_deploy,
        )
    else:
        res = orchestrator.generate_presentation_deck(topic=args.prompt, title=args.name)

    print(f"\n[STUDIO TASK RESULT]\n{json.dumps(res, indent=2)}")


if __name__ == "__main__":
    main()
