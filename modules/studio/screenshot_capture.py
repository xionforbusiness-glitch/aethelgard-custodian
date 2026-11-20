#!/usr/bin/env python3
"""
📸 Aethelgard Studio Headless Screenshot Capture
================================================
Captures high-resolution full-page viewport screenshots of deployed websites
via Playwright Chromium for instant visual inspection on Telegram.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("Aethelgard.ScreenshotCapture")


def capture_website_screenshot(
    url: str,
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
    full_page: bool = True,
    timeout_ms: int = 25000,
) -> Optional[Path]:
    """Navigates to live URL with headless Chromium and captures screenshot."""
    logger.info(f"📸 Capturing screenshot for {url} -> {output_path.name}...")
    try:
        from playwright.sync_api import sync_playwright

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1.5,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Wait briefly for CSS animations and fonts to settle
            page.wait_for_timeout(1500)

            page.screenshot(path=str(output_path), full_page=full_page)
            browser.close()

        if output_path.is_file() and output_path.stat().st_size > 1000:
            logger.info(f"✅ Screenshot captured successfully ({output_path.stat().st_size} bytes): {output_path}")
            return output_path
        else:
            logger.warning("Screenshot captured file is empty or missing.")
            return None
    except Exception as exc:
        logger.warning(f"Headless screenshot capture error for {url}: {exc}")
        return None
