#!/usr/bin/env python3
"""
🚀 Aethelgard Native Google Antigravity Pro OpenAI-Compatible Gateway (Port 20128)
Replaces the heavy Node.js OmniRoute daemon with a fast, zero-lockout Python server
that speaks directly to Google Antigravity (`daily-cloudcode-pa.googleapis.com`).

Features:
1. Full OpenAI `/v1/models` and `/v1/chat/completions` compatibility (SSE streaming + JSON).
2. Transparent legacy model remapping (`gemini-3.7-flash-*` -> `gemini-3.8-flash-*`).
3. Instant in-request multi-model failover across:
   `gemini-3.8-flash-low` -> `gemini-3.8-flash-medium` -> `claude-sonnet-5-5-low` -> `gemini-3.8-flash-tiered`
4. Full OpenAI `tools` / `tool_calls` <-> Antigravity `functionDeclarations` / `functionCall` / `functionResponse`
   translation with `skip_thought_signature_validator` and JSON schema sanitization.
5. Zero SQLite lockouts, zero Node.js 1GB RAM bloat, and automatic OAuth token caching & refresh.
"""

import os
import re
import sys
import json
import time
import uuid
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from antigravity_token_manager import fetch_fresh_google_token

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROXY_PORT = int(os.environ.get("OMNIROUTE_PORT", "20128"))
ANTIGRAVITY_PROJECT = os.environ.get("ANTIGRAVITY_PROJECT", "aicode-consumers")

UPSTREAM_URLS = [
    "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse",
    "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse",
]

UPSTREAM_TIMEOUT = int(os.environ.get("ANTIGRAVITY_UPSTREAM_TIMEOUT", "12"))
MAX_FAILOVER_BUDGET_SECONDS = int(os.environ.get("ANTIGRAVITY_FAILOVER_BUDGET", "30"))
MAX_FAILOVER_MODELS = int(os.environ.get("ANTIGRAVITY_MAX_FAILOVER_MODELS", "3"))


ANTIGRAVITY_DEFAULT_SYSTEM = (
    "You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team working on Advanced Agentic Coding.\n"
    "You are pair programming with a USER to solve their coding task. The task may require creating a new codebase, modifying or debugging an existing codebase, or simply answering a question.\n"
    "**Absolute paths only**\n"
    "**Proactiveness**"
)

MODEL_ALIAS_MAP = {
    # Legacy 3.7 -> 3.8
    "gemini-3.7-flash": "gemini-3.8-flash-low",
    "gemini-3.7-flash-low": "gemini-3.8-flash-low",
    "gemini-3.7-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.7-flash-high": "gemini-3.8-flash-high",
    "gemini-3.7-flash-tiered": "gemini-3.8-flash-tiered",
    # Short aliases
    "flash": "gemini-3.8-flash-low",
    "flash-low": "gemini-3.8-flash-low",
    "low": "gemini-3.8-flash-low",
    "fast": "gemini-3.8-flash-low",
    "chat": "gemini-3.8-flash-low",
    "casual": "gemini-3.8-flash-low",
    "gemini": "gemini-3.8-flash-low",
    "gemini-3.8-flash": "gemini-3.8-flash-low",
    "medium": "gemini-3.8-flash-medium",
    "flash-medium": "gemini-3.8-flash-medium",
    "high": "gemini-3.8-flash-high",
    "flash-high": "gemini-3.8-flash-high",
    "custodian": "gemini-3.8-flash-high",
    "vault": "gemini-3.8-flash-high",
    "thinking": "gemini-3.8-flash-high",
    "pro": "gemini-3.8-flash-high",
    # Legacy 3.5 / 3.6 -> 3.8
    "gemini-3.5-flash": "gemini-3.8-flash-low",
    "gemini-3.5-flash-low": "gemini-3.8-flash-low",
    "gemini-3.5-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.5-flash-high": "gemini-3.8-flash-high",
    "gemini-3.6-flash-low": "gemini-3.8-flash-low",
    "gemini-3.6-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.6-flash-high": "gemini-3.8-flash-high",
    # Claude aliases (bare Claude 5.5 404s on Antigravity; tier suffix is mandatory)
    "claude": "claude-sonnet-5-5-high",
    "sonnet": "claude-sonnet-5-5-high",
    "claude-sonnet-4-6": "claude-sonnet-5-5-high",
    "claude-sonnet-4-5": "claude-sonnet-5-5-high",
    "claude-sonnet-4-5-thinking": "claude-sonnet-5-5-high",
    "claude-sonnet-5-5": "claude-sonnet-5-5-high",
    "opus": "claude-opus-5-5-high",
    "claude-opus-4-6-thinking": "claude-opus-5-5-high",
    "claude-opus-4-5-thinking": "claude-opus-5-5-high",
    "claude-opus-5-5": "claude-opus-5-5-high",
    "gpt-oss-120b": "gpt-oss-120b-medium",
}

DEFAULT_FAILOVER_MODELS = [
    "gemini-3.8-flash-low",
    "gemini-3.8-flash-medium",
    "claude-sonnet-5-5-low",
    "gemini-3.8-flash-tiered",
    "gemini-3.8-flash-high",
    "claude-sonnet-5-5-high",
]

ADVERTISED_MODELS = [
    "gemini-3.8-flash-low",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-tiered",
    "claude-sonnet-5-5-low",
    "claude-sonnet-5-5-medium",
    "claude-sonnet-5-5-high",
    "claude-opus-5-5-low",
    "claude-opus-5-5-medium",
    "claude-opus-5-5-high",
    "gpt-oss-120b-medium",
    # Also advertise legacy IDs so client pre-checks never fail
    "gemini-3.7-flash-low",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-high",
]

BUILTIN_TOOL_NAMES = {"google_search", "web_search", "search_web", "googleSearch"}

# ── Token Cache ───────────────────────────────────────────────────────────────
_token_lock = threading.Lock()
_cached_token = None
_token_expires_at = 0.0


def get_valid_access_token(force_refresh: bool = False) -> str | None:
    global _cached_token, _token_expires_at
    now = time.time()
    with _token_lock:
        if not force_refresh and _cached_token and now < (_token_expires_at - 300):
            return _cached_token
        token, expires_in = fetch_fresh_google_token()
        if token:
            _cached_token = token
            _token_expires_at = now + max(expires_in, 600)
        return _cached_token


def normalize_model_id(raw_model: str) -> str:
    if not raw_model or not isinstance(raw_model, str):
        return "gemini-3.8-flash-low"
    m = raw_model.strip()
    for prefix in ("antigravity/", "agy/", "models/", "first-time/"):
        if m.lower().startswith(prefix):
            m = m[len(prefix):]
    if "/" in m:
        m = m.split("/")[-1]
    return MODEL_ALIAS_MAP.get(m, MODEL_ALIAS_MAP.get(m.lower(), m))


def build_failover_chain(requested_model: str) -> list[str]:
    primary = normalize_model_id(requested_model)
    chain = [primary]
    for fb in DEFAULT_FAILOVER_MODELS:
        if fb not in chain:
            chain.append(fb)
    return chain


# ── Schema & Message Translation (OpenAI -> Antigravity) ──────────────────────
ALLOWED_SCHEMA_KEYS = {
    "type", "description", "properties", "required", "items", "enum",
    "nullable", "anyOf", "format", "minimum", "maximum", "minItems", "maxItems"
}


def clean_json_schema(schema):
    """Sanitizes OpenAI JSON Schema into Gemini/Antigravity-compatible Schema object."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    cleaned = {}
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        non_null = [t for t in raw_type if t != "null"]
        cleaned["type"] = non_null[0] if non_null else "string"
        if "null" in raw_type:
            cleaned["nullable"] = True
    elif isinstance(raw_type, str):
        cleaned["type"] = raw_type

    if "description" in schema and isinstance(schema["description"], str):
        cleaned["description"] = schema["description"]

    if "enum" in schema and isinstance(schema["enum"], list):
        cleaned["enum"] = [str(v) for v in schema["enum"]]

    if "properties" in schema and isinstance(schema["properties"], dict):
        props = {}
        for k, v in schema["properties"].items():
            if isinstance(v, dict):
                props[k] = clean_json_schema(v)
        cleaned["properties"] = props
        if "type" not in cleaned:
            cleaned["type"] = "object"

    if "required" in schema and isinstance(schema["required"], list):
        valid_props = set(cleaned.get("properties", {}).keys())
        req_list = [r for r in schema["required"] if isinstance(r, str) and (not valid_props or r in valid_props)]
        if req_list:
            cleaned["required"] = req_list

    if "items" in schema:
        if isinstance(schema["items"], dict):
            cleaned["items"] = clean_json_schema(schema["items"])
        elif isinstance(schema["items"], list) and schema["items"]:
            cleaned["items"] = clean_json_schema(schema["items"][0])
        if "type" not in cleaned:
            cleaned["type"] = "array"

    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        non_null_variants = [
            v for v in schema["anyOf"]
            if isinstance(v, dict) and v.get("type") != "null"
        ]
        if len(non_null_variants) == 1:
            merged = clean_json_schema(non_null_variants[0])
            if "description" in cleaned and "description" not in merged:
                merged["description"] = cleaned["description"]
            cleaned = merged
            cleaned["nullable"] = True

    if "type" not in cleaned:
        cleaned["type"] = "object" if "properties" in cleaned else "string"

    return cleaned


def extract_text_from_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in ("text", "input_text") and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


def convert_openai_content_to_gemini_parts(content) -> list[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if isinstance(content, list):
        g_parts = []
        for item in content:
            if isinstance(item, str) and item:
                g_parts.append({"text": item})
            elif isinstance(item, dict):
                itype = item.get("type")
                if itype in ("text", "input_text") and item.get("text"):
                    g_parts.append({"text": str(item["text"])})
                elif itype == "image_url":
                    img_url = item.get("image_url", {})
                    url_str = img_url.get("url", "") if isinstance(img_url, dict) else str(img_url)
                    if url_str.startswith("data:"):
                        m = re.match(r"data:([^;]+);base64,(.+)", url_str, re.DOTALL)
                        if m:
                            g_parts.append({
                                "inlineData": {
                                    "mimeType": m.group(1),
                                    "data": m.group(2).strip()
                                }
                            })
        return g_parts
    return [{"text": str(content)}]


def build_antigravity_envelope(body: dict, upstream_model: str, include_tools: bool = True) -> dict:
    raw_messages = body.get("messages") or []
    is_claude = "claude" in upstream_model.lower()
    is_gpt_oss = "gpt-oss" in upstream_model.lower()

    # Map tool_call_id -> function name across history
    tc_id_to_name = {}
    for msg in raw_messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                if isinstance(tc, dict) and tc.get("id"):
                    fn = tc.get("function") or {}
                    if isinstance(fn, dict) and fn.get("name"):
                        tc_id_to_name[str(tc["id"])] = str(fn["name"])

    system_chunks = []
    contents = []

    for msg in raw_messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content")

        if role in ("system", "developer"):
            sys_txt = extract_text_from_content(content).strip()
            if sys_txt:
                system_chunks.append(sys_txt)
        elif role == "user":
            parts = convert_openai_content_to_gemini_parts(content)
            if parts:
                contents.append({"role": "user", "parts": parts})
        elif role == "assistant":
            parts = []
            txt = extract_text_from_content(content)
            if txt:
                parts.append({"text": txt})
            tool_calls = msg.get("tool_calls") or []
            if include_tools and isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    tc_id = str(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}")
                    fn = tc.get("function") or {}
                    fn_name = str(fn.get("name") or "tool")
                    raw_args = fn.get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                        except Exception:
                            parsed_args = {"raw": raw_args}
                    elif isinstance(raw_args, dict):
                        parsed_args = raw_args
                    else:
                        parsed_args = {}

                    if not isinstance(parsed_args, dict):
                        parsed_args = {"value": parsed_args}

                    parts.append({
                        "thoughtSignature": "skip_thought_signature_validator",
                        "functionCall": {
                            "id": tc_id,
                            "name": fn_name,
                            "args": parsed_args,
                        }
                    })
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            tc_id = str(msg.get("tool_call_id") or "")
            fn_name = msg.get("name") or tc_id_to_name.get(tc_id) or "tool_response"
            resp_txt = extract_text_from_content(content)
            if include_tools:
                try:
                    parsed_resp = json.loads(resp_txt) if resp_txt.strip().startswith(("{", "[")) else {"result": resp_txt}
                except Exception:
                    parsed_resp = {"result": resp_txt}
                if not isinstance(parsed_resp, dict):
                    parsed_resp = {"result": parsed_resp}
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "id": tc_id,
                            "name": str(fn_name),
                            "response": {"result": parsed_resp},
                        }
                    }]
                })
            else:
                contents.append({
                    "role": "user",
                    "parts": [{"text": f"[Tool Result ({fn_name})]: {resp_txt}"}]
                })

    # Relocate client system instructions into first user message (prevents Antigravity 429 on large system prompts)
    if system_chunks:
        combined_sys = "\n\n".join(system_chunks)
        sys_part = {"text": combined_sys}
        if contents and contents[0]["role"] == "user":
            contents[0]["parts"].insert(0, sys_part)
        else:
            contents.insert(0, {"role": "user", "parts": [sys_part]})

    if not contents:
        contents = [{"role": "user", "parts": [{"text": "Hello"}]}]

    # Merge consecutive same-role turns (required by Gemini/Vertex)
    merged_contents = []
    for c in contents:
        if not c.get("parts"):
            continue
        if merged_contents and merged_contents[-1]["role"] == c["role"]:
            merged_contents[-1]["parts"].extend(c["parts"])
        else:
            merged_contents.append({"role": c["role"], "parts": list(c["parts"])})

    # Strip trailing model turns (prevents 400 "Requests ending with a model turn are not supported")
    while len(merged_contents) > 1 and merged_contents[-1]["role"] == "model":
        merged_contents.pop()

    # Generation config
    gen_cfg = {
        "topK": int(body.get("top_k", 40)),
        "topP": float(body.get("top_p", 1.0)),
    }
    if body.get("temperature") is not None:
        gen_cfg["temperature"] = float(body["temperature"])

    req_max = body.get("max_tokens") or body.get("max_completion_tokens")
    if is_claude:
        gen_cfg["maxOutputTokens"] = min(int(req_max), 16384) if req_max else 16384
    elif req_max:
        gen_cfg["maxOutputTokens"] = min(int(req_max), 65536)

    # Thinking budget for Gemini 3.8 Flash tiers
    if not is_claude and not is_gpt_oss and "gemini" in upstream_model.lower():
        if upstream_model.endswith("-low"):
            budget = 1024
        elif upstream_model.endswith("-medium") or upstream_model.endswith("-tiered"):
            budget = 4096
        else:
            budget = 16384
        gen_cfg["thinkingConfig"] = {
            "thinkingBudget": budget,
            "includeThoughts": True,
        }
        if "maxOutputTokens" in gen_cfg and gen_cfg["maxOutputTokens"] <= budget:
            gen_cfg["maxOutputTokens"] = budget + 4096

    req_inner = {
        "sessionId": f"-{int(time.time() * 1000)}",
        "contents": merged_contents,
        "systemInstruction": {
            "role": "system",
            "parts": [{"text": ANTIGRAVITY_DEFAULT_SYSTEM}]
        },
        "generationConfig": gen_cfg,
    }

    # Convert tools if provided
    raw_tools = body.get("tools")
    if include_tools and isinstance(raw_tools, list) and raw_tools:
        fn_decls = []
        for t in raw_tools:
            if not isinstance(t, dict) or t.get("type") != "function":
                continue
            fn = t.get("function") or {}
            name = fn.get("name")
            if not name or name in BUILTIN_TOOL_NAMES:
                continue
            fn_decls.append({
                "name": str(name),
                "description": str(fn.get("description") or ""),
                "parameters": clean_json_schema(fn.get("parameters") or {"type": "object", "properties": {}}),
            })
        if fn_decls:
            req_inner["tools"] = [{"functionDeclarations": fn_decls}]
            req_inner["toolConfig"] = {"functionCallingConfig": {"mode": "VALIDATED"}}

    envelope = {
        "project": ANTIGRAVITY_PROJECT,
        "model": upstream_model,
        "userAgent": "antigravity",
        "requestType": "agent",
        "requestId": f"agent/{int(time.time() * 1000)}/{uuid.uuid4().hex[:10]}",
        "request": req_inner,
    }
    return envelope


def execute_antigravity_request(body: dict, requested_model: str) -> dict:
    """
    Executes the chat completion against Google Antigravity with automatic
    model failover and tool-schema fallback if needed.
    Returns:
      {
        "ok": True,
        "model": actual_model,
        "text": str,
        "reasoning": str,
        "tool_calls": list,
        "finish_reason": str,
        "usage": dict,
      }
    """
    chain = build_failover_chain(requested_model)[:MAX_FAILOVER_MODELS]
    token = get_valid_access_token(force_refresh=False)
    if not token:
        token = get_valid_access_token(force_refresh=True)
    if not token:
        return {"ok": False, "status": 503, "error": "Failed to refresh Google Antigravity OAuth token."}

    last_error = "Unknown upstream error"
    last_status = 503
    start_time = time.monotonic()

    for candidate_model in chain:
        if time.monotonic() - start_time > MAX_FAILOVER_BUDGET_SECONDS:
            last_error = f"Failover budget ({MAX_FAILOVER_BUDGET_SECONDS}s) exceeded"
            break
        # Try with tools first; if upstream returns 400 on tool schema, retry once with textual tool fallback
        tool_attempts = [True, False] if body.get("tools") else [False]
        for include_tools in tool_attempts:
            if time.monotonic() - start_time > MAX_FAILOVER_BUDGET_SECONDS:
                last_error = f"Failover budget ({MAX_FAILOVER_BUDGET_SECONDS}s) exceeded"
                break
            envelope = build_antigravity_envelope(body, candidate_model, include_tools=include_tools)
            payload_bytes = json.dumps(envelope).encode("utf-8")

            for url_idx, upstream_url in enumerate(UPSTREAM_URLS):
                if time.monotonic() - start_time > MAX_FAILOVER_BUDGET_SECONDS:
                    last_error = f"Failover budget ({MAX_FAILOVER_BUDGET_SECONDS}s) exceeded"
                    break
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "antigravity/ide/2.21.1 darwin/arm64",
                }
                req = urllib.request.Request(upstream_url, data=payload_bytes, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
                        raw_sse = resp.read().decode("utf-8", errors="replace")

                    text_chunks = []
                    reasoning_chunks = []
                    tool_calls = []
                    finish_reason = "stop"
                    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

                    for line in raw_sse.splitlines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            ev = json.loads(data_str)
                        except Exception:
                            continue

                        resp_obj = ev.get("response") or ev
                        if not isinstance(resp_obj, dict):
                            continue

                        um = resp_obj.get("usageMetadata")
                        if isinstance(um, dict):
                            usage = {
                                "prompt_tokens": int(um.get("promptTokenCount") or 0),
                                "completion_tokens": int(um.get("candidatesTokenCount") or 0),
                                "total_tokens": int(um.get("totalTokenCount") or 0),
                            }

                        candidates = resp_obj.get("candidates") or []
                        if not candidates or not isinstance(candidates[0], dict):
                            continue
                        cand = candidates[0]
                        content_obj = cand.get("content") or {}
                        parts = content_obj.get("parts") or []
                        for p in parts:
                            if not isinstance(p, dict):
                                continue
                            if "functionCall" in p and isinstance(p["functionCall"], dict):
                                fc = p["functionCall"]
                                fc_name = str(fc.get("name") or "")
                                if fc_name:
                                    fc_id = str(fc.get("id") or f"call_{uuid.uuid4().hex[:12]}")
                                    fc_args = fc.get("args") if fc.get("args") is not None else {}
                                    tool_calls.append({
                                        "id": fc_id,
                                        "index": len(tool_calls),
                                        "type": "function",
                                        "function": {
                                            "name": fc_name,
                                            "arguments": json.dumps(fc_args, ensure_ascii=False)
                                            if not isinstance(fc_args, str) else fc_args,
                                        }
                                    })
                                    finish_reason = "tool_calls"
                            elif isinstance(p.get("text"), str):
                                if p.get("thought"):
                                    reasoning_chunks.append(p["text"])
                                else:
                                    text_chunks.append(p["text"])

                        cand_finish = cand.get("finishReason")
                        if cand_finish and not tool_calls:
                            cf_lower = str(cand_finish).lower()
                            if cf_lower in ("max_tokens", "length"):
                                finish_reason = "length"
                            else:
                                finish_reason = "stop"

                    full_text = "".join(text_chunks)
                    full_reasoning = "".join(reasoning_chunks)

                    # Only accept if we got visible text, tool calls, or reasoning
                    if full_text or tool_calls or full_reasoning:
                        if not full_text and not tool_calls and full_reasoning:
                            full_text = full_reasoning
                        return {
                            "ok": True,
                            "model": candidate_model,
                            "text": full_text,
                            "reasoning": full_reasoning,
                            "tool_calls": tool_calls,
                            "finish_reason": "tool_calls" if tool_calls else finish_reason,
                            "usage": usage,
                        }
                    else:
                        last_error = f"Empty response stream from {candidate_model}"
                except urllib.error.HTTPError as he:
                    err_body = ""
                    try:
                        err_body = he.read().decode("utf-8", errors="replace")[:400]
                    except Exception:
                        pass
                    last_status = he.code
                    last_error = f"HTTP {he.code} on {candidate_model}: {err_body}"
                    print(f"[Antigravity Proxy] ⚠ {last_error}", flush=True)

                    if he.code in (401, 403):
                        token = get_valid_access_token(force_refresh=True) or token
                        continue
                    if he.code == 400 and include_tools:
                        # Break inner URL loop so we retry with include_tools=False
                        break
                    if he.code in (404, 429, 500, 502, 503, 504):
                        # Break inner URL loop and fail over immediately to next model in chain
                        break
                except Exception as ex:
                    last_error = f"Exception on {candidate_model}: {ex}"
                    print(f"[Antigravity Proxy] ⚠ {last_error}", flush=True)
                    continue

    return {"ok": False, "status": last_status, "error": last_error}


class AntigravityGatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status_code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models") or self.path.startswith("/models"):
            now_ts = int(time.time())
            data = []
            for m_id in ADVERTISED_MODELS:
                data.append({
                    "id": m_id,
                    "object": "model",
                    "created": now_ts,
                    "owned_by": "antigravity",
                })
                data.append({
                    "id": f"antigravity/{m_id}",
                    "object": "model",
                    "created": now_ts,
                    "owned_by": "antigravity",
                })
            self._send_json(200, {"object": "list", "data": data})
        elif self.path.startswith("/health") or self.path == "/":
            self._send_json(200, {"status": "ok", "engine": "aethelgard-antigravity-native-proxy"})
        else:
            self._send_json(404, {"error": {"message": f"Not found: {self.path}", "type": "not_found"}})

    def do_POST(self):
        if not (self.path.startswith("/v1/chat/completions") or self.path.startswith("/chat/completions")):
            self._send_json(404, {"error": {"message": f"Unsupported POST path: {self.path}"}})
            return

        content_len = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            body = json.loads(raw_body.decode("utf-8", errors="replace"))
        except Exception as e:
            self._send_json(400, {"error": {"message": f"Invalid JSON body: {e}"}})
            return

        requested_model = body.get("model") or "antigravity/gemini-3.8-flash-low"
        is_stream = bool(body.get("stream", False))

        result = execute_antigravity_request(body, requested_model)
        if not result.get("ok"):
            self._send_json(
                int(result.get("status") or 503),
                {
                    "error": {
                        "message": result.get("error", "Upstream provider error"),
                        "type": "upstream_error",
                        "code": result.get("status", 503),
                    }
                }
            )
            return

        cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:20]}"
        created_ts = int(time.time())
        actual_model = result["model"]
        text_out = result.get("text") or ""
        tool_calls = result.get("tool_calls") or []
        finish_reason = result.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
        usage = result.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        if not is_stream:
            msg_obj = {"role": "assistant", "content": text_out if text_out or not tool_calls else None}
            if tool_calls:
                msg_obj["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"],
                    }
                    for tc in tool_calls
                ]
            resp_payload = {
                "id": cmpl_id,
                "object": "chat.completion",
                "created": created_ts,
                "model": actual_model,
                "choices": [
                    {
                        "index": 0,
                        "message": msg_obj,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage,
            }
            self._send_json(200, resp_payload)
            return

        # Streaming SSE response (OpenAI format)
        chunks = []
        # 1. Initial role chunk
        chunks.append({
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": actual_model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        })
        # 2. Content delta chunk
        if text_out:
            chunks.append({
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": actual_model,
                "choices": [{"index": 0, "delta": {"content": text_out}, "finish_reason": None}],
            })
        # 3. Tool calls delta chunk(s)
        if tool_calls:
            chunks.append({
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": actual_model,
                "choices": [{"index": 0, "delta": {"tool_calls": tool_calls}, "finish_reason": None}],
            })
        # 4. Final finish_reason + usage chunk
        chunks.append({
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": actual_model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": usage,
        })

        sse_lines = [f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks]
        sse_lines.append("data: [DONE]\n\n")
        sse_bytes = "".join(sse_lines).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(sse_bytes)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(sse_bytes)


_proxy_server_instance = None


def start_antigravity_proxy_background(port: int = PROXY_PORT) -> ThreadingHTTPServer:
    """Starts the native Python Antigravity Gateway on 127.0.0.1:port in a daemon thread."""
    global _proxy_server_instance
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", port), AntigravityGatewayHandler)
    _proxy_server_instance = server
    t = threading.Thread(target=server.serve_forever, daemon=True, name="AntigravityNativeProxy")
    t.start()
    print(f"🚀 [Antigravity Native Proxy] Listening on http://127.0.0.1:{port}/v1 (Zero-Lockout Direct Engine)", flush=True)
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PROXY_PORT
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("127.0.0.1", port), AntigravityGatewayHandler)
    print(f"🚀 [Antigravity Native Proxy] Serving on http://127.0.0.1:{port}/v1 ...", flush=True)
    srv.serve_forever()
