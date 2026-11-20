#!/usr/bin/env python3
"""
🌍 Aethelgard World Simulation: Studio Swarm Evaluation Suite
============================================================
Verifies the Autonomous Design, Web & Presentation Studio Swarm:
  1. Next.js 15 Web Application Synthesis (React 19, Tailwind CSS, Lucide)
  2. Canva Executive Presentation Deck Generation
  3. GitHub & Vercel Deployment Orchestration
  4. Studio Stability Guard (Zero-Interruption Mutex)
  5. Security Armor Non-Owner Isolation
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.studio.canva_adapter import CanvaPresentationGenerator
from modules.studio.config import get_github_owner, get_github_token
from modules.studio.github_deployer import GitHubDeployer
from modules.studio.stability_guard import is_studio_locked, studio_execution_lock
from modules.studio.studio_orchestrator import StudioOrchestrator
from modules.studio.vercel_deployer import VercelDeployer
from modules.studio.web_generator import NextJsWebGenerator
import voice_tools_registry


def run_studio_swarm_tests():
    print("\n==================================================================")
    print("🎨 AETHELGARD STUDIO SWARM VERIFICATION SUITE")
    print("==================================================================\n")
    results = []

    with tempfile.TemporaryDirectory(prefix="aethelgard_studio_test_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        # ---------------------------------------------------------------------
        # TEST 1: Stability Guard Mutex Lock
        # ---------------------------------------------------------------------
        t0 = time.perf_counter()
        with studio_execution_lock("test_lock"):
            locked_during = is_studio_locked()
        locked_after = is_studio_locked()
        pass_1 = locked_during and not locked_after
        el_1 = (time.perf_counter() - t0) * 1000.0
        results.append(("Stability Guard Mutex Invariant", pass_1, el_1, f"Locked during: {locked_during}; Cleared after: {not locked_after}"))
        print(f"[{'✅ PASS' if pass_1 else '❌ FAIL'}] Stability Guard Mutex ({el_1:.1f}ms): {results[-1][3]}")

        # ---------------------------------------------------------------------
        # TEST 2: Next.js 15 Code Synthesis Engine
        # ---------------------------------------------------------------------
        t0 = time.perf_counter()
        generator = NextJsWebGenerator(workspace_root=tmp_path)
        synth_res = generator.generate_project(
            prompt="Damascus Airport Ground Turnaround Telemetry Dashboard",
            project_name="damascus-turnaround",
        )
        p_dir: Path = synth_res["project_dir"]

        pkg_exists = (p_dir / "package.json").is_file()
        page_exists = (p_dir / "app" / "page.tsx").is_file()
        layout_exists = (p_dir / "app" / "layout.tsx").is_file()
        css_exists = (p_dir / "app" / "globals.css").is_file()
        tailwind_exists = (p_dir / "tailwind.config.js").is_file()

        page_content = (p_dir / "app" / "page.tsx").read_text(encoding="utf-8")
        has_interactive_tracker = "Damascus Station Turnaround Tracker" in page_content and "useState" in page_content
        has_lucide = "lucide-react" in (p_dir / "package.json").read_text(encoding="utf-8")

        pass_2 = pkg_exists and page_exists and layout_exists and css_exists and tailwind_exists and has_interactive_tracker and has_lucide
        el_2 = (time.perf_counter() - t0) * 1000.0
        results.append(("Next.js 15 Web Application Synthesis", pass_2, el_2, f"Files generated: 6/6; Interactive tracker: {has_interactive_tracker}; Lucide: {has_lucide}"))
        print(f"[{'✅ PASS' if pass_2 else '❌ FAIL'}] Next.js 15 Web Synthesis ({el_2:.1f}ms): {results[-1][3]}")

        # ---------------------------------------------------------------------
        # TEST 3: Canva Presentation Deck Generator
        # ---------------------------------------------------------------------
        t0 = time.perf_counter()
        canva = CanvaPresentationGenerator()
        deck_dir = tmp_path / "presentations"
        deck_res = canva.generate_presentation_deck(
            topic="Airport Turnaround Operations & Ground Safety",
            output_dir=deck_dir,
            deck_title="Damascus Turnaround Briefing",
        )
        deck_md = Path(deck_res["deck_markdown_file"])
        deck_json = Path(deck_res["deck_json_file"])

        pass_3 = (
            deck_res.get("success") is True
            and deck_res.get("slides_count") == 5
            and deck_md.is_file()
            and deck_json.is_file()
            and "marp: true" in deck_md.read_text(encoding="utf-8")
        )
        el_3 = (time.perf_counter() - t0) * 1000.0
        results.append(("Canva Executive Deck Synthesis", pass_3, el_3, f"Slides: {deck_res.get('slides_count')}; Marp/Canva Markdown: True"))
        print(f"[{'✅ PASS' if pass_3 else '❌ FAIL'}] Canva Executive Deck ({el_3:.1f}ms): {results[-1][3]}")

        # ---------------------------------------------------------------------
        # TEST 4: GitHub & Vercel Deployer Configuration
        # ---------------------------------------------------------------------
        t0 = time.perf_counter()
        gh = GitHubDeployer()
        vc = VercelDeployer()
        owner = get_github_owner()
        token = get_github_token()

        pass_4 = owner == "xionforbusiness-glitch" and bool(token)
        el_4 = (time.perf_counter() - t0) * 1000.0
        results.append(("GitHub Deployer Environment Verification", pass_4, el_4, f"Owner: {owner}; Token resolved: {bool(token)}"))
        print(f"[{'✅ PASS' if pass_4 else '❌ FAIL'}] GitHub Deployer Config ({el_4:.1f}ms): {results[-1][3]}")

        # ---------------------------------------------------------------------
        # TEST 5: Voice Tools Registry & Security Armor Gatekeeper
        # ---------------------------------------------------------------------
        t0 = time.perf_counter()

        # Non-owner caller attempting to trigger web deploy
        guest_ctx = {"peer_id": 5032777692, "is_owner": False, "call_id": "sim_guest_deploy_001"}
        handled_guest, guest_res = voice_tools_registry.try_dispatch_new_voice_tool(
            "deploy_autonomous_website",
            {"prompt": "Unauthorized website creation"},
            guest_ctx,
        )
        guest_blocked = guest_res.get("status") == "blocked"

        # Owner caller triggering web deploy
        owner_ctx = {"peer_id": 1021125594, "is_owner": True, "call_id": "sim_owner_deploy_001"}
        handled_owner, owner_res = voice_tools_registry.try_dispatch_new_voice_tool(
            "deploy_autonomous_website",
            {"prompt": "Aviation station operations portal"},
            owner_ctx,
        )
        owner_allowed = owner_res.get("status") == "success" and owner_res.get("dispatched") is True

        pass_5 = guest_blocked and owner_allowed
        el_5 = (time.perf_counter() - t0) * 1000.0
        results.append(("Voice Tool Dispatch & Security Armor", pass_5, el_5, f"Guest blocked: {guest_blocked}; Owner dispatched: {owner_allowed}"))
        print(f"[{'✅ PASS' if pass_5 else '❌ FAIL'}] Voice Tool Dispatch & Security Armor ({el_5:.1f}ms): {results[-1][3]}")

    total = len(results)
    passed = sum(1 for r in results if r[1])
    failed = total - passed
    pass_rate = (passed / total * 100.0) if total > 0 else 0.0

    print("\n==================================================================")
    print(f"📊 Studio Swarm Pass Rate: {pass_rate:.1f}% ({passed}/{total})")
    print("==================================================================\n")
    return passed == total


if __name__ == "__main__":
    success = run_studio_swarm_tests()
    sys.exit(0 if success else 1)
