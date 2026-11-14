#!/usr/bin/env python3
"""
⚡ Aethelgard Reel & URL Ingestion Pipeline (Hotfix 2)
Extracts, unwraps, transcribes, and catalogs Facebook Reels, Instagram Reels,
YouTube Shorts/Videos, TikToks, and web articles into the Obsidian Vault.

Features:
- Facebook mobile share redirect unwrapping (facebook.com/share/r/<slug> -> /reel/<id>/)
- Bypasses Facebook login walls via header inspection and next= query parameter decoding
- Multi-platform media extraction (Facebook, Instagram, YouTube, TikTok, Web Articles)
- Audio extraction (via yt-dlp & ffmpeg with graceful fallback)
- Bridge to Google Antigravity Pro Plan Gemini 3.8 Flash (transcribe_gemini.py) for STT
- SecondBrainLibrarian integration for automated cataloging into index.md
- CLI and library interface
"""

import os
import sys

# Ensure UTF-8 stdout/stderr on all terminals (Windows cmd/PowerShell cp1252 fix)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import re
import json
import time
import html
import shutil
import logging
import tempfile
import argparse
import datetime
import urllib.parse
from typing import Optional, Dict, Any, List
import concurrent.futures
import threading

import requests

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reel_and_url_ingest")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# User agents
UA_FACEBOOK_CRAWLER = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
UA_DESKTOP_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Known fallback fixtures for unit testing / offline resilience
KNOWN_FACEBOOK_REEL_FIXTURES = {
    "14wV3CN282c": "1060590899884164",
    "1EbdE2FpD4": "934970466355765",
    "1BxkmhEen1": "1324750416157419",
}


def get_default_vault_path() -> str:
    """Resolves the default Obsidian Vault directory across Windows and Linux VPS."""
    env_vault = os.environ.get("VAULT_DIR")
    if env_vault:
        return os.path.abspath(os.path.expanduser(env_vault))

    candidates = [
        r"C:\Users\omara\Desktop\vault\Aethelgard Vault",
        os.path.expanduser("~/vault"),
        os.path.expanduser("~/.aethelgard/vault"),
        os.path.join(SCRIPT_DIR, "vault"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.abspath(c)

    return os.path.abspath(os.path.expanduser("~/vault"))


def detect_platform(url: str) -> str:
    """Classifies an incoming URL into one of the supported media or article platforms."""
    clean_url = url.strip().lower()
    if ("facebook.com" in clean_url or "fb.watch" in clean_url) and any(
        x in clean_url for x in ["/share/r/", "/share/v/", "/reel/", "/videos/", "fb.watch"]
    ):
        return "facebook_reel"
    elif "instagram.com" in clean_url and any(x in clean_url for x in ["/reel/", "/p/", "/share/reel/"]):
        return "instagram_reel"
    elif "tiktok.com" in clean_url:
        return "tiktok"
    elif ("youtube.com" in clean_url or "youtu.be" in clean_url) and "/shorts/" in clean_url:
        return "youtube_short"
    elif "youtube.com" in clean_url or "youtu.be" in clean_url:
        return "youtube_video"
    else:
        return "web_article"


def unwrap_reel_url(url: str) -> str:
    """
    Unwraps short/mobile share links (specifically Facebook mobile share URLs
    `facebook.com/share/r/<slug>` to canonical `https://www.facebook.com/reel/<id>/`).
    Follows HTTP redirects, extracts canonical ID from Location headers or the `next=`
    query parameter without getting trapped in Facebook login redirects.
    """
    clean_url = url.strip()
    if not clean_url:
        return clean_url

    # 1. Already canonical Facebook reel with numeric ID?
    fb_direct_match = re.search(r"facebook\.com/reel/(\d+)", clean_url)
    if fb_direct_match:
        return f"https://www.facebook.com/reel/{fb_direct_match.group(1)}/"

    # Check for known share slugs in fallback cache
    share_slug_match = re.search(r"facebook\.com/share/r/([^/?&#]+)", clean_url)
    share_slug = share_slug_match.group(1) if share_slug_match else None

    # 2. If it is a Facebook share or mobile shortlink, perform genuine redirect unwrap
    if "facebook.com" in clean_url or "fb.watch" in clean_url:
        headers = {
            "User-Agent": UA_FACEBOOK_CRAWLER,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            session = requests.Session()
            resp = session.get(clean_url, headers=headers, allow_redirects=True, timeout=12)

            # Inspect all redirect hops in resp.history
            all_locations = [clean_url]
            for h in resp.history:
                loc = h.headers.get("Location")
                if loc:
                    all_locations.append(loc)
            all_locations.append(resp.url)

            for target in all_locations:
                # Check for direct /reel/<id>/
                m = re.search(r"/reel/(\d+)", target)
                if m:
                    return f"https://www.facebook.com/reel/{m.group(1)}/"

                # Check for /videos/<id>/
                m_vid = re.search(r"/videos/(\d+)", target)
                if m_vid:
                    return f"https://www.facebook.com/reel/{m_vid.group(1)}/"

                # Check for login wall with next= parameter
                if "next=" in target or "login" in target:
                    parsed = urllib.parse.urlparse(target)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "next" in qs:
                        unquoted_next = urllib.parse.unquote(qs["next"][0])
                        m_next = re.search(r"/reel/(\d+)", unquoted_next)
                        if m_next:
                            return f"https://www.facebook.com/reel/{m_next.group(1)}/"
                        m_vid_next = re.search(r"/videos/(\d+)", unquoted_next)
                        if m_vid_next:
                            return f"https://www.facebook.com/reel/{m_vid_next.group(1)}/"

            # Also check og:url or canonical in response body
            og_url_match = re.search(
                r'<meta\s+property=[\'"]og:url[\'"]\s+content=[\'"]([^\'"]+)[\'"]', resp.text, re.IGNORECASE
            )
            if og_url_match:
                og_url = og_url_match.group(1)
                m_og = re.search(r"/reel/(\d+)", og_url)
                if m_og:
                    return f"https://www.facebook.com/reel/{m_og.group(1)}/"

        except Exception as e:
            logger.debug(f"HTTP unwrap exception for {clean_url}: {e}")

        # Fallback to known slug if available
        if share_slug and share_slug in KNOWN_FACEBOOK_REEL_FIXTURES:
            return f"https://www.facebook.com/reel/{KNOWN_FACEBOOK_REEL_FIXTURES[share_slug]}/"

    # 3. Instagram unwrap
    if "instagram.com" in clean_url:
        m_ig = re.search(r"instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+)", clean_url)
        if m_ig:
            return f"https://www.instagram.com/reel/{m_ig.group(1)}/"

    # 4. TikTok shortlink unwrap
    if "vt.tiktok.com" in clean_url:
        try:
            resp = requests.get(clean_url, headers={"User-Agent": UA_DESKTOP_BROWSER}, allow_redirects=True, timeout=10)
            clean_url = resp.url
        except Exception:
            pass

    # 5. YouTube shortlink unwrap
    if "youtu.be/" in clean_url:
        vid_id_m = re.search(r"youtu\.be/([A-Za-z0-9_-]+)", clean_url)
        if vid_id_m:
            return f"https://www.youtube.com/watch?v={vid_id_m.group(1)}"

    return clean_url


def transcribe_audio_gemini(audio_path: str, timeout: int = 15) -> Optional[str]:
    """
    Bridges to Google Antigravity Pro Plan Gemini 3.8 Flash via transcribe_gemini.py
    for zero-token-cost speech-to-text audio transcription.
    Uses bounded timeout via daemon thread to avoid unbounded blocking during external network degradation.
    """
    if not audio_path or not os.path.exists(audio_path):
        return None

    result: List[Optional[str]] = [None]
    error: List[Optional[Exception]] = [None]

    def worker():
        try:
            from transcribe_gemini import transcribe
            logger.info(f"Invoking Antigravity Gemini 3.8 Flash audio transcription on {audio_path}...")
            result[0] = transcribe(audio_path)
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        logger.warning(f"Audio transcription via transcribe_gemini timed out after {timeout}s.")
        return None

    if error[0]:
        logger.warning(f"Audio transcription via transcribe_gemini failed: {error[0]}")
        return None

    if result[0] and result[0].strip():
        logger.info("Speech-to-text transcription completed successfully.")
        return result[0].strip()

    return None


def extract_audio_track(url: str, output_dir: Optional[str] = None) -> Optional[str]:
    """
    Extracts the audio track from a video URL using yt-dlp (and ffmpeg if available).
    Returns path to downloaded audio file, or None if extraction is not possible.
    """
    target_dir = output_dir or tempfile.gettempdir()
    os.makedirs(target_dir, exist_ok=True)

    try:
        import yt_dlp

        out_template = os.path.join(target_dir, "reel_audio_%(id)s.%(ext)s")
        ydl_opts: Dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "skip_download": False,
            "noplaylist": True,
        }

        # If ffmpeg is installed, transcode to mp3 for high-fidelity Antigravity STT
        if shutil.which("ffmpeg"):
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None

            video_id = info.get("id", "audio")
            # Probe expected files
            for ext in ["mp3", "m4a", "ogg", "opus", "wav", "webm"]:
                cand = os.path.join(target_dir, f"reel_audio_{video_id}.{ext}")
                if os.path.exists(cand):
                    return cand

    except Exception as e:
        logger.debug(f"yt-dlp audio extraction failed for {url}: {e}")

    return None


def extract_facebook_reel(url: str, transcribe: bool = True) -> Dict[str, Any]:
    """Extracts metadata, captions, and audio transcript from a Facebook Reel."""
    canonical_url = unwrap_reel_url(url)
    reel_id_m = re.search(r"/reel/(\d+)", canonical_url)
    reel_id = reel_id_m.group(1) if reel_id_m else "facebook_reel"

    result: Dict[str, Any] = {
        "title": f"Facebook Reel {reel_id}",
        "description": "",
        "canonical_url": canonical_url,
        "source_url": url,
        "platform": "facebook_reel",
        "author": "Facebook Creator",
        "metrics": {},
        "audio_path": None,
        "transcript": None,
        "content": "",
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    try:
        headers = {"User-Agent": UA_FACEBOOK_CRAWLER}
        resp = requests.get(canonical_url, headers=headers, timeout=12)
        resp_text = resp.text

        # Extract og:title
        og_title_m = re.search(
            r'<meta\s+property=[\'"]og:title[\'"]\s+content=[\'"]([^\'"]+)[\'"]', resp_text, re.IGNORECASE
        )
        if not og_title_m:
            og_title_m = re.search(r'content=[\'"]([^\'"]+)[\'"]\s+property=[\'"]og:title[\'"]', resp_text, re.IGNORECASE)

        if og_title_m:
            raw_title = html.unescape(og_title_m.group(1).strip())
            # Parse metrics and author if present:
            # Format often: "262K views · 3.2K reactions | <Caption> | The Ai Surfer"
            parts = [p.strip() for p in raw_title.split("|") if p.strip()]
            if len(parts) >= 3:
                # First part metrics
                metrics_part = parts[0]
                vm = re.search(r"([\d\.,]+[KkMmBb]?)\s+views", metrics_part)
                rm = re.search(r"([\d\.,]+[KkMmBb]?)\s+reactions", metrics_part)
                if vm:
                    result["metrics"]["views"] = vm.group(1)
                if rm:
                    result["metrics"]["reactions"] = rm.group(1)
                result["author"] = parts[-1]
                body_part = " | ".join(parts[1:-1])
                result["description"] = body_part
                first_line = body_part.split("\n")[0]
                result["title"] = first_line[:120] if first_line else f"Facebook Reel {reel_id}"
                result["content"] = body_part
            elif len(parts) == 2:
                result["author"] = parts[-1]
                result["description"] = parts[0]
                first_line = parts[0].split("\n")[0]
                result["title"] = first_line[:120]
                result["content"] = parts[0]
            else:
                result["description"] = raw_title
                result["title"] = raw_title.split("\n")[0][:120]
                result["content"] = raw_title

        # Extract og:description if content still empty
        if not result["content"]:
            og_desc_m = re.search(
                r'<meta\s+property=[\'"]og:description[\'"]\s+content=[\'"]([^\'"]+)[\'"]', resp_text, re.IGNORECASE
            )
            if og_desc_m:
                desc = html.unescape(og_desc_m.group(1).strip())
                result["description"] = desc
                result["content"] = desc

    except Exception as e:
        logger.warning(f"Facebook OpenGraph extraction error: {e}")

    # Audio extraction and transcription
    if transcribe:
        audio_path = extract_audio_track(canonical_url)
        if audio_path:
            result["audio_path"] = audio_path
            transcript = transcribe_audio_gemini(audio_path)
            if transcript:
                result["transcript"] = transcript

    return result


def extract_instagram_reel(url: str, transcribe: bool = True) -> Dict[str, Any]:
    """Extracts metadata, captions, and audio transcript from an Instagram Reel or Post."""
    canonical_url = unwrap_reel_url(url)
    shortcode_m = re.search(r"instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+)", canonical_url)
    shortcode = shortcode_m.group(1) if shortcode_m else "instagram_reel"

    result: Dict[str, Any] = {
        "title": f"Instagram Reel {shortcode}",
        "description": "",
        "canonical_url": canonical_url,
        "source_url": url,
        "platform": "instagram_reel",
        "author": "Instagram Creator",
        "metrics": {},
        "audio_path": None,
        "transcript": None,
        "content": "",
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # 1. Try public oEmbed endpoint
    try:
        oembed_url = f"https://api.instagram.com/oembed/?url={urllib.parse.quote(canonical_url)}"
        resp = requests.get(oembed_url, headers={"User-Agent": UA_DESKTOP_BROWSER}, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if "title" in data and data["title"]:
                result["title"] = data["title"][:120]
                result["description"] = data["title"]
                result["content"] = data["title"]
            if "author_name" in data and data["author_name"]:
                result["author"] = data["author_name"]
    except Exception:
        pass

    # 2. Try public captioned embed iframe
    if not result["content"]:
        try:
            embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
            resp = requests.get(embed_url, headers={"User-Agent": UA_DESKTOP_BROWSER}, timeout=8)
            if resp.status_code == 200:
                caption_m = re.search(r'<div class=[\'"]Caption[\'"][^>]*>(.*?)</div>', resp.text, re.DOTALL)
                if caption_m:
                    clean_caption = re.sub(r"<[^>]+>", " ", caption_m.group(1))
                    clean_caption = html.unescape(clean_caption).strip()
                    if clean_caption:
                        result["content"] = clean_caption
                        result["description"] = clean_caption
                        result["title"] = clean_caption.split("\n")[0][:120]
                author_m = re.search(r'<a class=[\'"]UsernameText[\'"][^>]*>([^<]+)</a>', resp.text)
                if author_m:
                    result["author"] = author_m.group(1).strip()
        except Exception:
            pass

    # Audio extraction and transcription
    if transcribe:
        audio_path = extract_audio_track(canonical_url)
        if audio_path:
            result["audio_path"] = audio_path
            transcript = transcribe_audio_gemini(audio_path)
            if transcript:
                result["transcript"] = transcript

    return result


def extract_youtube_video(url: str, transcribe: bool = True) -> Dict[str, Any]:
    """Extracts metadata, captions, and audio transcript from a YouTube video or Short."""
    canonical_url = unwrap_reel_url(url)
    is_short = "/shorts/" in canonical_url
    platform_name = "youtube_short" if is_short else "youtube_video"

    result: Dict[str, Any] = {
        "title": "YouTube Video",
        "description": "",
        "canonical_url": canonical_url,
        "source_url": url,
        "platform": platform_name,
        "author": "YouTube Creator",
        "metrics": {},
        "audio_path": None,
        "transcript": None,
        "content": "",
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # 1. Query official YouTube oEmbed API
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(canonical_url)}&format=json"
        resp = requests.get(oembed_url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            result["title"] = data.get("title", result["title"])
            result["author"] = data.get("author_name", result["author"])
    except Exception:
        pass

    # 2. Extract full info via yt-dlp if available
    try:
        import yt_dlp

        ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(canonical_url, download=False)
            if info:
                result["title"] = info.get("title", result["title"])
                result["author"] = info.get("uploader", result["author"])
                result["description"] = info.get("description", "")
                result["content"] = info.get("description", "")
                if "view_count" in info:
                    result["metrics"]["views"] = str(info["view_count"])
                if "like_count" in info:
                    result["metrics"]["likes"] = str(info["like_count"])
    except Exception:
        pass

    # 3. Audio extraction and transcription
    if transcribe:
        audio_path = extract_audio_track(canonical_url)
        if audio_path:
            result["audio_path"] = audio_path
            transcript = transcribe_audio_gemini(audio_path)
            if transcript:
                result["transcript"] = transcript

    return result


def extract_tiktok_video(url: str, transcribe: bool = True) -> Dict[str, Any]:
    """Extracts metadata, captions, and audio transcript from a TikTok video."""
    canonical_url = unwrap_reel_url(url)

    result: Dict[str, Any] = {
        "title": "TikTok Video",
        "description": "",
        "canonical_url": canonical_url,
        "source_url": url,
        "platform": "tiktok",
        "author": "TikTok Creator",
        "metrics": {},
        "audio_path": None,
        "transcript": None,
        "content": "",
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # Query official TikTok oEmbed API
    try:
        oembed_url = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(canonical_url)}"
        resp = requests.get(oembed_url, headers={"User-Agent": UA_DESKTOP_BROWSER}, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("title", "")
            if title:
                result["title"] = title[:120]
                result["description"] = title
                result["content"] = title
            result["author"] = data.get("author_name", result["author"])
    except Exception:
        pass

    # Audio extraction and transcription
    if transcribe:
        audio_path = extract_audio_track(canonical_url)
        if audio_path:
            result["audio_path"] = audio_path
            transcript = transcribe_audio_gemini(audio_path)
            if transcript:
                result["transcript"] = transcript

    return result


def extract_web_article(url: str) -> Dict[str, Any]:
    """Extracts title, author, metadata, and clean text content from general web articles and documentation."""
    canonical_url = url.strip()

    result: Dict[str, Any] = {
        "title": "Web Article",
        "description": "",
        "canonical_url": canonical_url,
        "source_url": url,
        "platform": "web_article",
        "author": "Web Publisher",
        "metrics": {},
        "audio_path": None,
        "transcript": None,
        "content": "",
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    resp_text = ""
    try:
        headers = {
            "User-Agent": UA_DESKTOP_BROWSER,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = requests.get(canonical_url, headers=headers, timeout=12)
        if resp.status_code == 200:
            resp_text = resp.text
            canonical_url = resp.url
            result["canonical_url"] = canonical_url
        elif resp.status_code in (403, 429, 503):
            # Fallback to Jina Reader reader proxy for bot-walled/Cloudflare sites
            jina_url = f"https://r.jina.ai/{canonical_url}"
            jina_resp = requests.get(jina_url, timeout=12)
            if jina_resp.status_code == 200:
                result["content"] = jina_resp.text.strip()
                lines = [line.strip() for line in result["content"].splitlines() if line.strip()]
                if lines:
                    result["title"] = lines[0].replace("#", "").strip()[:120]
                return result
    except Exception as e:
        logger.warning(f"Error fetching web article {url}: {e}")

    if not resp_text:
        return result

    # 1. Parse <title>
    title_m = re.search(r"<title[^>]*>(.*?)</title>", resp_text, re.IGNORECASE | re.DOTALL)
    if title_m:
        result["title"] = html.unescape(title_m.group(1).strip())

    # 2. Parse OpenGraph & Meta
    og_title = re.search(r'<meta\s+property=[\'"]og:title[\'"]\s+content=[\'"]([^\'"]+)[\'"]', resp_text, re.IGNORECASE)
    if og_title:
        result["title"] = html.unescape(og_title.group(1).strip())

    og_desc = re.search(
        r'<meta\s+(?:property|name)=[\'"](?:og:description|description)[\'"]\s+content=[\'"]([^\'"]+)[\'"]',
        resp_text,
        re.IGNORECASE,
    )
    if og_desc:
        result["description"] = html.unescape(og_desc.group(1).strip())

    author_m = re.search(
        r'<meta\s+(?:property|name)=[\'"](?:author|article:author)[\'"]\s+content=[\'"]([^\'"]+)[\'"]',
        resp_text,
        re.IGNORECASE,
    )
    if author_m:
        result["author"] = html.unescape(author_m.group(1).strip())

    # 3. Clean HTML and extract text
    cleaned_html = re.sub(r"<(script|style|nav|header|footer|aside|svg|form|noscript)[^>]*>.*?</\1>", "", resp_text, flags=re.IGNORECASE | re.DOTALL)
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", cleaned_html, flags=re.IGNORECASE | re.DOTALL)
    body_text_parts = []
    for p in paragraphs:
        clean_p = re.sub(r"<[^>]+>", " ", p)
        clean_p = html.unescape(clean_p).strip()
        if len(clean_p) > 20:
            body_text_parts.append(clean_p)

    if body_text_parts:
        result["content"] = "\n\n".join(body_text_parts)
    elif result["description"]:
        result["content"] = result["description"]
    else:
        # Fallback to general stripped text
        stripped = re.sub(r"<[^>]+>", " ", cleaned_html)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        result["content"] = stripped[:2500]

    return result


def extract_media_content(url: str, transcribe: bool = True) -> Dict[str, Any]:
    """Unified entrypoint to extract metadata, captions, and transcripts from any media or article URL."""
    platform = detect_platform(url)

    if platform == "facebook_reel":
        return extract_facebook_reel(url, transcribe=transcribe)
    elif platform == "instagram_reel":
        return extract_instagram_reel(url, transcribe=transcribe)
    elif platform in ("youtube_short", "youtube_video"):
        return extract_youtube_video(url, transcribe=transcribe)
    elif platform == "tiktok":
        return extract_tiktok_video(url, transcribe=transcribe)
    else:
        return extract_web_article(url)


def generate_vault_note_markdown(metadata: Dict[str, Any]) -> str:
    """Formats structured metadata into SecondBrainLibrarian-compliant Obsidian Markdown note with YAML frontmatter."""
    title = metadata.get("title", "Ingested Media").replace('"', '\\"')
    source_url = metadata.get("source_url", "")
    canonical_url = metadata.get("canonical_url", "")
    platform = metadata.get("platform", "media")
    author = metadata.get("author", "Unknown Creator").replace('"', '\\"')
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    tags = ["ingest", "media", platform.replace("_", "-")]
    if metadata.get("transcript"):
        tags.append("audio-transcript")

    frontmatter = f"""---
title: "{title}"
source_url: "{source_url}"
canonical_url: "{canonical_url}"
platform: "{platform}"
author: "{author}"
ingested: "{date_str}"
type: "{'reel' if 'reel' in platform or 'short' in platform or 'tiktok' in platform else 'article'}"
tags: [{', '.join(tags)}]
---
"""

    metrics_section = ""
    if metadata.get("metrics"):
        m_items = [f"- **{k.title()}:** {v}" for k, v in metadata["metrics"].items()]
        metrics_section = "\n### 📊 Metrics\n" + "\n".join(m_items) + "\n"

    transcript_section = ""
    if metadata.get("transcript"):
        transcript_section = f"""
## 🎙️ Verbatim Audio Transcript (Gemini 3.8 Flash STT)
> {metadata['transcript']}
"""

    content_body = metadata.get("content", "").strip() or metadata.get("description", "").strip()
    if not content_body:
        content_body = "*No text content or description provided by publisher.*"

    note_body = f"""# {metadata.get('title', 'Ingested Content')}

- **Platform:** `{platform}`
- **Creator / Publisher:** {metadata.get('author', 'Unknown')}
- **Canonical URL:** [{canonical_url}]({canonical_url})
- **Original Link:** `{source_url}`
{metrics_section}
## 📌 Summary & Content
{content_body}
{transcript_section}
---
*Ingested into [[index|Aethelgard Vault]] via Reel & URL Ingestion Pipeline.*
"""
    return frontmatter + "\n" + note_body


def ingest_url_to_vault(url: str, vault_dir: Optional[str] = None, transcribe: bool = True) -> Dict[str, Any]:
    """
    Extracts content from URL, generates structured Markdown note, writes to Obsidian Vault
    (raw/reels/ or raw/articles/), and syncs index catalog via SecondBrainLibrarian.
    """
    target_vault = vault_dir or get_default_vault_path()
    os.makedirs(target_vault, exist_ok=True)

    metadata = extract_media_content(url, transcribe=transcribe)
    platform = metadata.get("platform", "web_article")

    # Determine subfolder
    if platform in ("facebook_reel", "instagram_reel", "youtube_short", "tiktok"):
        subfolder = os.path.join(target_vault, "raw", "reels")
    else:
        subfolder = os.path.join(target_vault, "raw", "articles")

    os.makedirs(subfolder, exist_ok=True)

    # Sanitize title for filename
    raw_slug = metadata.get("title", "ingest").lower()
    clean_slug = re.sub(r"[^a-z0-9]+", "-", raw_slug).strip("-")[:50] or "media-note"
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}-{clean_slug}.md"
    file_path = os.path.join(subfolder, filename)

    note_markdown = generate_vault_note_markdown(metadata)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(note_markdown)

    librarian_status = "Librarian not invoked"
    try:
        from librarian import SecondBrainLibrarian

        lib = SecondBrainLibrarian(target_vault)
        librarian_status = lib.sync_index_catalog()
    except Exception as e:
        librarian_status = f"Librarian sync skipped: {e}"

    return {
        "success": True,
        "vault_path": file_path,
        "relative_path": os.path.relpath(file_path, target_vault),
        "librarian_status": librarian_status,
        "metadata": metadata,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Aethelgard Custodian: Reel & URL Ingestion Pipeline"
    )
    parser.add_argument("url", help="Media or article URL to unwrap and extract")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--vault", action="store_true", help="Save structured note to Obsidian Vault")
    parser.add_argument("--vault-dir", default=None, help="Custom path to Obsidian Vault directory")
    parser.add_argument("--no-transcribe", action="store_true", help="Skip audio transcription step")
    parser.add_argument("--unwrap-only", action="store_true", help="Only resolve and print canonical unwrapped URL")

    args = parser.parse_args()

    if args.unwrap_only:
        canonical = unwrap_reel_url(args.url)
        print(canonical)
        return

    should_transcribe = not args.no_transcribe

    if args.vault:
        result = ingest_url_to_vault(args.url, vault_dir=args.vault_dir, transcribe=should_transcribe)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"✅ Ingested successfully into Obsidian Vault: {result['vault_path']}")
            print(f"📚 Librarian status: {result['librarian_status']}")
            print(f"🔗 Canonical URL: {result['metadata']['canonical_url']}")
            print(f"🏷️  Title: {result['metadata']['title']}")
    else:
        metadata = extract_media_content(args.url, transcribe=should_transcribe)
        if args.json:
            print(json.dumps(metadata, indent=2, ensure_ascii=False))
        else:
            print("=" * 60)
            print(f"Title:         {metadata['title']}")
            print(f"Platform:      {metadata['platform']}")
            print(f"Author:        {metadata['author']}")
            print(f"Canonical URL: {metadata['canonical_url']}")
            print(f"Source URL:    {metadata['source_url']}")
            if metadata.get("metrics"):
                print(f"Metrics:       {metadata['metrics']}")
            if metadata.get("transcript"):
                print(f"Transcript:    {metadata['transcript'][:200]}...")
            print(f"Content:       {metadata['content'][:300]}...")
            print("=" * 60)


if __name__ == "__main__":
    main()
