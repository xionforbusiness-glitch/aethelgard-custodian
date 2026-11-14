---
name: reel-and-url-ingest
description: Extract, unwrap, transcribe, and catalog Facebook Reels, Instagram Reels, YouTube Shorts/videos, TikTok videos, and web articles into the Obsidian Vault.
version: 1.0.0
author: Aethelgard Custodian
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [reel, video, media-ingest, obsidian, stt, research, youtube, facebook, instagram, tiktok]
    category: research
    related_skills: [llm-wiki, obsidian]
---

# Reel & URL Ingestion Pipeline

Use this skill whenever Omar or a user sends a video, reel, short link, or web article URL to `@aethelgard_vault_bot` or requests ingestion, transcription, summarization, or cataloging of media into the Aethelgard Obsidian Vault.

---

## Capabilities & Supported Platforms

1. **Facebook Reels & Mobile Shares:**
   - Detects and unwraps mobile share links (`https://www.facebook.com/share/r/<slug>/` and `fb.watch`) to canonical `https://www.facebook.com/reel/<id>/`.
   - Bypasses Facebook login walls by intercepting HTTP redirect locations and decoding `next=` query parameters.
   - Extracts rich OpenGraph captions, publisher/author identities, and engagement metrics (views, reactions).

2. **Instagram Reels & Posts:**
   - Handles `instagram.com/reel/<shortcode>/` and `instagram.com/p/<shortcode>/`.
   - Extracts captions, author handles, and thumbnail metadata via public oEmbed and captioned embed wrappers without requiring Instagram credentials.

3. **YouTube Shorts & Videos:**
   - Handles `youtube.com/shorts/<id>`, `youtu.be/<id>`, and `youtube.com/watch?v=<id>`.
   - Retrieves metadata, video descriptions, view counts, and likes via oEmbed and `yt-dlp`.

4. **TikTok Videos:**
   - Unwraps shortlinks (`vt.tiktok.com/...`) and queries official TikTok oEmbed for author, captions, and sound metadata.

5. **General Web Articles & Documentation:**
   - Scrapes title, author, publication timestamp, and clean body paragraphs from any article, blog post, or documentation page.
   - Features automatic fallback to reader proxies (Jina Reader) for Cloudflare or bot-walled sites.

6. **Zero-Token-Cost Audio Transcription:**
   - Downloads audio tracks via `yt-dlp` and `ffmpeg`.
   - Bridges directly to Google Antigravity Pro Plan Gemini 3.8 Flash (`transcribe_gemini.py`) for instantaneous, native speech-to-text with zero token charges and bilingual (Arabic/English) support.

7. **Automated Obsidian Vault Cataloging:**
   - Ingests structured notes with YAML frontmatter into `raw/reels/` (for video reels/shorts) or `raw/articles/` (for web documents).
   - Invokes `SecondBrainLibrarian.sync_index_catalog()` to immediately link new notes into `index.md`.

---

## Tool & CLI Commands

### 1. Ingest Directly into Obsidian Vault (Recommended)
Extracts metadata, transcribes audio, writes the Markdown note to `raw/reels/` or `raw/articles/`, and indexes it in `index.md`:
```bash
python reel_and_url_ingest.py "<URL>" --vault --json
```

### 2. Extract Metadata & Transcripts as JSON (Without Saving to Vault)
Inspect content, captions, and transcript payload directly:
```bash
python reel_and_url_ingest.py "<URL>" --json
```

### 3. Quick URL Unwrap
Resolve a mobile share link without extracting full media:
```bash
python reel_and_url_ingest.py "<URL>" --unwrap-only
```

### 4. Skip Transcription (Metadata Only)
When only post text, titles, or descriptions are needed:
```bash
python reel_and_url_ingest.py "<URL>" --no-transcribe --json
```

---

## Python API Usage

The pipeline can also be called directly within Custodian modules:

```python
from reel_and_url_ingest import unwrap_reel_url, extract_media_content, ingest_url_to_vault

# 1. Unwrap Facebook share redirect
canonical_url = unwrap_reel_url("https://www.facebook.com/share/r/14wV3CN282c/")
# Returns: "https://www.facebook.com/reel/1060590899884164/"

# 2. Extract media and transcripts
data = extract_media_content(canonical_url, transcribe=True)
print("Title:", data["title"])
print("Transcript:", data.get("transcript"))

# 3. Ingest note into Obsidian Vault
result = ingest_url_to_vault("https://example.com", transcribe=False)
print("Saved to:", result["vault_path"])
print("Librarian:", result["librarian_status"])
```

---

## Workflow for Hermes Agent

When Omar sends a link in Telegram:
1. **Identify Link:** Check if the message contains a supported link (Facebook, Instagram, YouTube, TikTok, or web URL).
2. **Execute Ingestion:** Run `python reel_and_url_ingest.py "<URL>" --vault --json`.
3. **Parse Output:** Inspect the returned JSON payload.
4. **Respond to Omar:** Provide a concise Telegram summary:
   - 🎬 Title / Headline
   - 👤 Creator / Publisher
   - 📊 Metrics (views, reactions) if available
   - 💡 3 Key Takeaways from the reel / article / transcript
   - 📁 Confirm note ingestion into Obsidian Vault (`raw/reels/...` or `raw/articles/...`) and cataloging in `index.md`.
