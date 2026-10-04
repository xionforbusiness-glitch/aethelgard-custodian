#!/usr/bin/env python3
"""
📞 Aethelgard Live Bidirectional Voice Bridge
Powered by Gemini 3.8 Live API (gemini-3.8-live) over WebSockets.
Enables hands-free, real-time voice calls directly in mobile or desktop browsers.
"""

import os
import sys
import json
import base64
import asyncio
import datetime
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import websockets
from websockets.asyncio.server import serve
from google import genai
from google.genai import types

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(SCRIPT_DIR, "call_interface.html")
HTTP_PORT = int(os.environ.get("PORT_LIVE_CALL", 20130))
WS_PORT = int(os.environ.get("PORT_LIVE_WS", 20131))

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "AQ.Ab8RN6I64Bj-z2kKo2b-o6cETMuJgQ0LySbUjAMqgraCrYKPzQ")
VAULT_DIR = os.path.abspath(os.path.expanduser(os.environ.get("VAULT_DIR", "~/vault")))


class CallHttpHandler(BaseHTTPRequestHandler):
    """Serves the glowing audio orb call interface and health checks."""

    def log_message(self, format, *args):
        pass  # Quiet HTTP logs

    def do_GET(self):
        if self.path in ("/", "/call", "/index.html"):
            if os.path.exists(HTML_PATH):
                with open(HTML_PATH, "r", encoding="utf-8") as f:
                    content = f.read()

                # Dynamically inject WebSocket port into HTML client
                # If behind reverse proxy or specific host, keep same host with WS_PORT
                script_inject = f"""
                <script>
                  window.AETHELGARD_WS_PORT = {WS_PORT};
                </script>
                """
                content = content.replace("</head>", f"{script_inject}\n</head>")
                # Adjust WS url in script
                content = content.replace("window.location.host + '/ws/call'", f"window.location.hostname + ':{WS_PORT}'")

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"call_interface.html not found.")
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"aethelgard-live-voice"}')
        else:
            self.send_response(404)
            self.end_headers()


def start_http_server():
    """Runs lightweight HTTP server on daemon thread."""
    server = HTTPServer(("0.0.0.0", HTTP_PORT), CallHttpHandler)
    print(f"📞 Live Voice Call Web UI listening on http://0.0.0.0:{HTTP_PORT}/call")
    server.serve_forever()


async def handle_browser_client(websocket):
    """Bridges browser Web Audio streams directly with Gemini 3.8 Live API."""
    print("[Live Call] Browser connected to WebSocket voice bridge.")
    client = genai.Client(api_key=GOOGLE_API_KEY)

    system_instruction = (
        "You are Aethelgard, the personal autonomous intelligence companion and knowledge custodian for Omar Elnemr. "
        "You are currently on a live, hands-free phone conversation with him. "
        "Speak in a deep, natural, authentic British male JARVIS tone, or switch to Syrian Arabic dialect if Omar speaks in Arabic. "
        "Be concise, engaging, direct, and warm. "
        "Keep your spoken answers brief (1-3 sentences) suitable for real-time natural dialogue. "
        "You are aware of his airport shift schedules at Damascus Airport, his aviation passion, and his Obsidian vault."
    )

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name="Charon"  # Deep, natural British/command timbre
                )
            )
        ),
        system_instruction=types.Content(
            parts=[types.Part(text=system_instruction)]
        )
    )

    conversation_log = []
    session_start = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        async with client.aio.live.connect(model="gemini-3.8-live", config=config) as session:
            print("[Live Call] Connected to Gemini 3.8 Live API session.")

            async def browser_to_gemini():
                """Forwards 16kHz PCM audio chunks from browser mic to Gemini Live."""
                try:
                    async for raw_msg in websocket:
                        try:
                            msg = json.loads(raw_msg)
                            if msg.get("type") == "audio":
                                pcm_b64 = msg.get("data", "")
                                pcm_bytes = base64.b64decode(pcm_b64)
                                await session.send_realtime_input(
                                    audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
                                )
                        except Exception as parse_err:
                            pass
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    print(f"⚠ browser_to_gemini notice: {e}")

            async def gemini_to_browser():
                """Forwards 24kHz PCM audio chunks and transcripts from Gemini Live to browser."""
                try:
                    async for response in session.receive():
                        content = response.server_content
                        if not content:
                            continue

                        # 1. Spoken audio from Gemini
                        if content.model_turn:
                            for part in content.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    audio_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                                    await websocket.send(json.dumps({
                                        "type": "audio",
                                        "data": audio_b64
                                    }))

                        # 2. Interruption detection
                        if content.interrupted:
                            await websocket.send(json.dumps({"type": "interrupted"}))

                        # 3. Transcriptions
                        if content.input_transcription and content.input_transcription.text:
                            text = content.input_transcription.text
                            conversation_log.append(f"**Omar:** {text}")
                            await websocket.send(json.dumps({"type": "transcript", "text": text}))

                        if content.output_transcription and content.output_transcription.text:
                            text = content.output_transcription.text
                            conversation_log.append(f"**Aethelgard:** {text}")
                            await websocket.send(json.dumps({"type": "transcript", "text": text}))

                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    print(f"⚠ gemini_to_browser notice: {e}")

            # Run both streaming directions concurrently
            b2g = asyncio.create_task(browser_to_gemini())
            g2b = asyncio.create_task(gemini_to_browser())

            done, pending = await asyncio.wait([b2g, g2b], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()

    except Exception as conn_err:
        print(f"[Live Call] Live session ended: {conn_err}")
    finally:
        print("[Live Call] Browser session closed.")
        # Save transcript to Obsidian Vault
        if conversation_log:
            try:
                date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                transcripts_dir = os.path.join(VAULT_DIR, "raw", "transcripts")
                os.makedirs(transcripts_dir, exist_ok=True)
                note_path = os.path.join(transcripts_dir, f"{date_str}-live-call.md")

                log_body = "\n\n".join(conversation_log)
                markdown_content = (
                    f"# 🎙️ Live Voice Call Transcript ({session_start})\n\n"
                    f"**Session:** Gemini 3.8 Live Bidirectional Call\n"
                    f"**Participants:** Omar Elnemr & Aethelgard Custodian\n\n"
                    f"---\n\n"
                    f"{log_body}\n"
                )
                with open(note_path, "a", encoding="utf-8") as f:
                    f.write("\n\n" + markdown_content)
                print(f"[Live Call] Saved call transcript to {note_path}")
            except Exception as save_err:
                print(f"⚠ Failed to save call transcript: {save_err}")


async def run_ws_server():
    """Runs WebSocket bridge server."""
    print(f"📞 Live Voice Call WebSocket listening on ws://0.0.0.0:{WS_PORT}")
    async with serve(handle_browser_client, "0.0.0.0", WS_PORT):
        await asyncio.Future()  # Run forever


def start_live_call_server_background():
    """Starts both HTTP UI server and WebSocket bridge in background threads."""
    t_http = threading.Thread(target=start_http_server, daemon=True)
    t_http.start()

    def run_ws():
        asyncio.run(run_ws_server())

    t_ws = threading.Thread(target=run_ws, daemon=True)
    t_ws.start()
    return t_http, t_ws


if __name__ == "__main__":
    t_http = threading.Thread(target=start_http_server, daemon=True)
    t_http.start()
    print("🚀 Starting Aethelgard Live Call Server...")
    asyncio.run(run_ws_server())
