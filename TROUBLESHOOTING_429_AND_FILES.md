# Aethelgard Troubleshooting & Incident Postmortem (2026-11-17)

## 1. True Root Cause of `HTTP 429 RESOURCE_EXHAUSTED` on `daily-cloudcode-pa.googleapis.com`

Initial testing misattributed the `HTTP 429` rejections to a ~22.5 KiB payload limit. Deep chunk-by-chunk probing subsequently proved:

- A clean **60,254-byte** payload returns **`200 OK`** on `daily-cloudcode-pa.googleapis.com`.
- The `HTTP 429 RESOURCE_EXHAUSTED` error is triggered specifically by **Google's server-side access-control check rejecting third-party Hermes Agent system prompt signatures** (`You run on Hermes Agent (by Nous Research)...`, `# Google model operational directives`, etc.) on the internal Antigravity IDE OAuth endpoint.
- Truncating `effective_sys` to 6,000 characters temporarily masked the error only because Hermes appends its harness prompt after `SOUL.md` (~char 8,000+), which simultaneously cut off parts of `SOUL.md` and is not a sanctioned/sustainable fix.

### Sanctioned Permanent Solution
To run Hermes Agent with full context, zero truncation, and zero upstream blocks, configure Hermes (`~/.hermes/profiles/llm-wiki/config.yaml` and `.env`) to use a **documented, sanctioned API provider** instead of routing Hermes through the internal Antigravity IDE endpoint:

1. **Google Gemini API (Official — Google AI Studio / Vertex AI):**
   - Generate an official API key from [Google AI Studio](https://aistudio.google.com/apikey) or Vertex AI.
   - Supports full 1M context window and tool calling natively with zero harness restrictions.
2. **Anthropic API / OpenRouter / Kaggle Ollama Tunnel:**
   - Standard OpenAI-compatible or Anthropic endpoints with full system prompt support.

> [!NOTE]
> The **Telegram Voice Caller (`telegram_voice_caller.py`)** uses its own compact voice prompt and does not inject Hermes harness directives, which is why voice calls were unaffected by the Hermes block.

---

## 2. Why the Bot Acted "Weird" & Replied *"All systems green"* Instead of Exporting the PDF

Inspecting `state.db` (`ID 3568`–`3578`) exposed two separate bugs in the bot pipeline:

1. **Conversation Amnesia Bug in `antigravity_proxy.py`:**
   - Under the temporary `19,500`-byte cap, `tools` (~12 KB) + `effective_sys` (6 KB) left only ~900 bytes for conversation history.
   - The pruning loop (`drop_idx = 1 if len(merged_contents) > 3 else 0`) preserved `merged_contents[0]` (the very first message from hours earlier: `"Hello Aethelgard, status report?"`) while deleting Omar's latest `"export it as PDF"` request and splitting active `functionCall`/`functionResponse` pairs.
   - **Fix applied:** Restored `TARGET_ENVELOPE_BUDGET` default to `45,000` bytes and fixed compaction to always drop from the **oldest** turns (`pop(0)`) until landing on a clean non-`functionResponse` `user` turn, preserving the latest request and active tool calls.

2. **Missing PDF/DOCX Libraries & General Telegram File Sender:**
   - The Cloud VM venv only had PDF readers (`pypdf`, `PyPDF2`) — `reportlab`, `python-docx`, `weasyprint`, and `libreoffice` were not installed (`ModuleNotFoundError: No module named 'reportlab'`).
   - Only `shift_tracker.py` had a Telegram `sendDocument` call; there was no general-purpose file sender CLI.
   - **Fix applied:**
     - Installed `reportlab` and `python-docx` into `/home/omar_alnemr04/aethelgard-custodian/venv/`.
     - Added `send_telegram_file.py` (`python3 send_telegram_file.py <path> --caption "<text>"`).
     - Documented `reportlab`, `python-docx`, and `send_telegram_file.py` in `profile/SOUL.md`.
     - Upgraded `edge-tts` from `7.2.7` to `7.3.1` to resolve Bing WebSocket 403 errors.
