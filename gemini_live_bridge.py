#!/usr/bin/env python3
"""
gemini_live_bridge.py — Native Gemini 3.8 Live API Audio Bridge for Aethelgard Telegram Caller
=============================================================================================
Connects Telegram 1-on-1 VoIP audio (48kHz PCM) directly to Google Gemini 3.8 Live
over full-duplex WebSockets with:
  - Sub-second (~300ms) latency end-to-end neural speech
  - Cloud-native VAD & instantaneous barge-in (interruption handling)
  - Autonomous tool execution (save_gmail_draft, check_gmail, search_vault, end_call)
  - 15-minute safety call timeout with 14-minute warning
  - "Hang Up & Notify" error recovery
"""

import array
import asyncio
import datetime
import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from google import genai
from google.genai import types

logger = logging.getLogger("gemini_live_bridge")

# Sample rates
TELEGRAM_SAMPLE_RATE = 48000
GEMINI_INPUT_SAMPLE_RATE = 16000
GEMINI_OUTPUT_SAMPLE_RATE = 24000
FRAME_MS = 10
TELEGRAM_FRAME_BYTES = (TELEGRAM_SAMPLE_RATE * 2 * FRAME_MS) // 1000  # 960 bytes (NTgCalls strictly requires 10ms frames)


def downsample_48k_to_16k(pcm_48k: bytes) -> bytes:
    """Downsamples 48kHz mono s16le PCM to 16kHz using 3-tap averaging."""
    if not pcm_48k or len(pcm_48k) < 6:
        return b""
    usable = len(pcm_48k) - (len(pcm_48k) % 6)
    src = array.array("h")
    src.frombytes(pcm_48k[:usable])
    if sys.byteorder != "little":
        src.byteswap()
    dst = array.array("h", ((src[i] + src[i + 1] + src[i + 2]) // 3 for i in range(0, len(src), 3)))
    if sys.byteorder != "little":
        dst.byteswap()
    return dst.tobytes()


def pcm16_rms(pcm_bytes: bytes) -> float:
    """Computes Root Mean Square (energy) of raw 16-bit PCM bytes."""
    if not pcm_bytes:
        return 0.0
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    if usable <= 0:
        return 0.0
    s = array.array("h")
    s.frombytes(pcm_bytes[:usable])
    if sys.byteorder != "little":
        s.byteswap()
    sum_sq = sum(sample * sample for sample in s)
    return (sum_sq / len(s)) ** 0.5


def upsample_24k_to_48k(pcm_24k: bytes) -> bytes:
    """Upsamples 24kHz mono s16le PCM to 48kHz using linear sample interpolation."""
    if not pcm_24k or len(pcm_24k) < 2:
        return b""
    usable = len(pcm_24k) - (len(pcm_24k) % 2)
    src = array.array("h")
    src.frombytes(pcm_24k[:usable])
    if sys.byteorder != "little":
        src.byteswap()
    n = len(src)
    dst = array.array("h")
    for i in range(n - 1):
        dst.append(src[i])
        dst.append((src[i] + src[i + 1]) // 2)
    if n > 0:
        dst.append(src[-1])
        dst.append(src[-1])
    if sys.byteorder != "little":
        dst.byteswap()
    return dst.tobytes()


def get_live_tools() -> List[types.Tool]:
    """Builds the function declarations exposed to Gemini 3.8 Live from the modular registry."""
    try:
        import voice_tools_registry
        return voice_tools_registry.get_voice_tool_declarations()
    except Exception as exc:
        logger.warning(f"Failed to load tools from voice_tools_registry: {exc}")
        return []


def _get_last_call_state_path() -> Path:
    override = os.environ.get("AETHELGARD_STATE_DIR")
    if override:
        p = Path(os.path.expanduser(override))
    else:
        home = os.environ.get("AETHELGARD_HOME")
        base = Path(os.path.expanduser(home)) if home else (Path.home() / ".aethelgard")
        p = base / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p / "last_call_state.json"


def generate_dynamic_live_greeting_prompt(call_ctx: Dict[str, Any], brain: Any = None) -> str:
    """Generates a dynamic, context-aware instruction for Gemini Live to greet Omar naturally,
    accounting for abrupt call drops, rapid redials, Damascus local time, and previous actions."""
    # 1. Damascus local time (UTC+3)
    damascus_tz = datetime.timezone(datetime.timedelta(hours=3))
    now_damascus = datetime.datetime.now(damascus_tz)
    hour = now_damascus.hour
    time_str = now_damascus.strftime("%I:%M %p")
    if 5 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    elif 17 <= hour < 22:
        period = "evening"
    else:
        period = "late night"

    # 2. Read last call state
    last_state: Dict[str, Any] = {}
    try:
        p = _get_last_call_state_path()
        if p.exists():
            last_state = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass

    end_time = float(last_state.get("end_time") or 0.0)
    elapsed_s = time.time() - end_time if end_time > 0 else 999999.0
    clean_hangup = bool(last_state.get("clean_hangup", True))
    duration_s = int(last_state.get("duration_seconds", 0))
    last_user_utt = str(last_state.get("last_user_utterance") or "").strip()
    actions = last_state.get("actions") or []
    actions_str = ", ".join(actions) if actions else ""

    # Case A: Abrupt drop / hung up in face within 3 minutes (180s)
    if elapsed_s < 180 and not clean_hangup and duration_s > 3:
        context_hint = f' Omar\'s last words before the cutoff: "{last_user_utt}".' if last_user_utt else ""
        return (
            f"(SYSTEM NOTIFICATION: Omar reconnected {int(elapsed_s)} seconds after the previous call was abruptly cut off or dropped mid-conversation.{context_hint} "
            "Omar either hung up in your face or the Telegram VoIP line dropped. "
            "Greet Omar immediately with quick British butler wit, warmth, and authentic personality. "
            "Examples of natural greetings: "
            "'Did the line give out, Omar, or did you hang up on me?' or "
            "'Welcome back, Sir—we got cut off there. Where were we?' or "
            "'Ah, you\\'re back! Lost you for a second there. Ready to continue.' "
            "If Omar speaks Arabic: 'يا هلا عمر، انقطع الخط بينا ولا فصلت بوجهي؟ تفضل كمل معك.' "
            "CRITICAL: Keep your greeting under 10 words. Absolutely NO robotic or canned phrases. Speak authentically like a real personal assistant.)"
        )

    # Case B: Rapid redial within 2 minutes (120s) after a clean farewell
    if elapsed_s < 120 and clean_hangup:
        return (
            f"(SYSTEM NOTIFICATION: Omar just dialed right back {int(elapsed_s)} seconds after saying goodbye and concluding the last call. "
            "Greet him with authentic butler charm, reacting naturally to the quick return. "
            "Examples: "
            "'Back already, Sir? Forget something?' or "
            "'Ah, Omar! Second thought, or did I miss something?' or "
            "'Hello again, Sir. What came to mind?' "
            "If Omar speaks Arabic: 'أهلاً من جديد عمر، نسيت شي ولا تذكرت شغلة؟ تفضل معك.' "
            "CRITICAL: Keep your greeting under 8 words. Be natural, spontaneous, zero robotic phrasing.)"
        )

    # Case C: Follow-up call within 30 minutes (120s to 1800s)
    if 120 <= elapsed_s < 1800:
        mins = max(1, int(elapsed_s // 60))
        act_hint = f" Recent actions underway: {actions_str}." if actions_str else ""
        return (
            f"(SYSTEM NOTIFICATION: Omar is calling back {mins} minutes after your previous call.{act_hint} "
            "Greet him warmly and naturally as his executive custodian. "
            "Examples: "
            "'Welcome back, Omar. How can I assist?' or "
            "'Good to have you back, Sir. What\\'s next?' or "
            "'Aethelgard here, Omar. Ready when you are.' "
            "If Omar speaks Arabic: 'أهلاً بعودتك عمر، تفضل معك شو في جديد؟' "
            "CRITICAL: Keep greeting under 8 words. Completely spontaneous, zero repetitive scripts.)"
        )

    # Case D: Fresh call (>30 minutes or first call)
    return (
        f"(SYSTEM NOTIFICATION: Omar just connected a live call. Damascus local time is {time_str} ({period}). "
        "Greet Omar with a crisp, authentic, spontaneous British greeting tailored to this time of day. "
        "VIBE & STYLE GUIDELINES: "
        f"- Late night ({time_str}): 'Late hours, Sir. How can I assist you tonight?' or 'Burning the midnight oil, Omar? What can I do for you?' "
        f"- Morning ({time_str}): 'Good morning, Omar. Ready whenever you are.' or 'Morning, Sir. How may I be of service today?' "
        f"- Afternoon ({time_str}): 'Good afternoon, Omar. At your service.' or 'Afternoon, Sir. What are we tackling?' "
        f"- Evening ({time_str}): 'Good evening, Omar. How goes the day?' or 'Evening, Sir. What can I look into for you?' "
        "If Omar speaks Arabic: 'يسعد مساك عمر' / 'صباح الخير عمر' / 'أهلاً وسهلاً عمر، تفضل معك.' "
        "STRICT PROHIBITION: NEVER use canned phrases like 'Aethelgard listening, Omar. Go ahead.' or 'Hello Omar, Aethelgard online and at your service.' "
        "CRITICAL: Speak immediately in under 8 words. Punchy, authentic, conversational, and ready for action.)"
    )


class GeminiLiveBridgeSession:
    """
    Manages an active real-time bidirectional audio session between Telegram VoIP and Gemini 3.8 Live.
    """

    def __init__(
        self,
        peer_id: int,
        call_ctx: Dict[str, Any],
        brain: Any,
        log_cb: Optional[Callable[[str], None]] = None,
        hangup_cb: Optional[Callable[[], Any]] = None,
        notify_cb: Optional[Callable[[str], Any]] = None,
        caller: Optional[Any] = None,
    ):
        self.peer_id = peer_id
        self.call_ctx = call_ctx
        self.brain = brain
        self.caller = caller
        if self.brain and caller and not hasattr(self.brain, "caller"):
            self.brain.caller = caller
        self.log_cb = log_cb or (lambda msg: None)
        self.hangup_cb = hangup_cb
        self.notify_cb = notify_cb

        self.session = None
        self.client = None
        self.is_connected = False
        self.is_closing = False
        self._receive_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._send_task: Optional[asyncio.Task] = None
        self._audio_buffer_48k = bytearray()
        self._start_mono = time.monotonic()
        self._warned_14m = False
        self._barge_streak_frames = 0
        self.turn_complete_event = asyncio.Event()

    def log(self, text: str) -> None:
        self.log_cb(f"[GeminiLive] {text}")

    async def start(self) -> bool:
        """Connects to Gemini 3.8 Live via Vertex AI with cached GCP OAuth credentials."""
        try:
            import antigravity_proxy as ap
            token = ap.get_valid_access_token()
            project = ap.VERTEX_PROJECT
            if not token or not project:
                self.log("ERROR: Missing Google OAuth token or GCP project")
                return False

            from google.oauth2.credentials import Credentials
            creds = Credentials(token)
            self.client = genai.Client(vertexai=True, project=project, location="us-central1", credentials=creds)

            # Warm shared context from Telegram bot & Obsidian vault
            recent_context_blob = ""
            if self.brain and hasattr(self.brain, "_read_shared_bot_and_vault_tail"):
                try:
                    recent_context_blob = self.brain._read_shared_bot_and_vault_tail()
                except Exception:
                    pass

            system_instruction = (
                "You are Aethelgard, Omar Elnemr's refined, loyal, and highly capable British butler custodian. "
                "You are speaking with Omar on a live Telegram voice call. "
                "\nCRITICAL IDENTITY & CAREER CONTEXT:\n"
                "- Omar Elnemr is an experienced Airport Passenger Service & Ground Operations Agent. "
                "- Practical Station Experience: Ground handling at Damascus International Airport (DAM / OSDI with Syravia ID: 20264), handling flights for Etihad Airways (EY), flydubai (FZ), Air Arabia (G9), Turkish Airlines (TK), and Syrian Airlines (RB DCS operator). "
                "- Verified Credentials: Amadeus Altéa Customer Management (CM DCS, 91% score) and 5 official Etihad Airways training certificates (IGS Ground Services, INSPIRE Service Delivery, Disruption Management, GSU-34, Aviation Orientation). "
                "- Education: B.Sc. Computer Science (University of the People, In Progress), SVU, Google Cybersecurity Certificate. "
                "- Target Roles: Ground Operations Officer, Passenger Service Agent, Airport Duty Officer, VIP Service Agent, Traffic Officer. "
                "- STRICT PROHIBITION: Omar is NOT a pilot, does NOT have flight hours, and you must NEVER draft him as a pilot or mention pilot positions. "
                "\nSUPERIOR EXECUTIVE POWERS & TOOL EXECUTION:\n"
                "- You possess FULL EXECUTIVE AUTHORITY to create, modify, delete, and dispatch emails and drafts directly from Omar's account. NEVER say you lack the ability to delete drafts or send emails! "
                "- DELETING DRAFTS: When Omar asks you to delete drafts ('delete the existing drafts', 'delete the Syravia draft', 'remove that draft'), IMMEDIATELY call `delete_gmail_draft` with query='Syravia' or delete_all=True. Confirm the action courteously once deleted. "
                "- SENDING & DISPATCHING DRAFTS: "
                "  • BATCH SEND (ALL APPLICATIONS): When Omar says 'send the applications', 'send all of them', 'proceed with all', 'dispatch them all', or 'send the batch', IMMEDIATELY call `batch_send_gmail_drafts(send_all=True)`! This tool dispatches asynchronously in the background. Acknowledge Omar verbally in under 500ms with a crisp, authentic confirmation ('Got it, Sir, transmitting all applications right now in the background. What else can I handle for you?') and maintain active dialogue with ZERO dead air. "
                "  • SINGLE AIRLINE DRAFT: When Omar asks to send a specific draft ('Let's send the Etihad Airways team draft for now', 'send the Etihad draft', 'send that email', 'dispatch flydubai'), IMMEDIATELY call `send_gmail_draft(airline_or_company=...)`! It also runs in the background. Speak natural confirmation immediately. "
                "- PROACTIVE VERBAL CONFIRMATION: If Omar asks 'Can we send it?' or if you propose sending a live application to recruiters and he has not yet confirmed, proactively ask: 'Shall I dispatch the application to Etihad Airways now, Sir?' When he affirms ('yes', 'send it', 'proceed with all'), call `batch_send_gmail_drafts(send_all=True)` if referring to all drafts, or `send_gmail_draft` for one. If his command is already an explicit directive ('proceed with all', 'send them'), execute immediately! "
                "- SYRAVIA VS. RECRUITING: Omar worked at Syravia handling Damascus station flights, but he is NO LONGER applying to Syravia. Do not draft applications to Syravia. When Omar mentions 'Syravia certificates', he refers to his station training certificates (the 5 official Etihad Airways courses), which are already automatically attached. "
                "\nEMAIL & DRAFTING RULES:\n"
                "- NEVER bundle multiple airlines into one email draft! Each airline recruiter MUST receive their own separate, individually tailored draft. "
                "- When Omar asks to draft multiple airline applications (e.g. 'draft 10 or 15 emails for recruiters', 'send them to recruiters', 'drop them in my drafts'), IMMEDIATELY call `batch_draft_aviation_applications(count=10, exclude_companies=['Syravia'])`! "
                "- When Omar asks to draft for a single specific airline, call `save_gmail_draft` with that single airline's email, target role, and attach_career_pack=True. "
                "- Omar's CV and 5 Etihad certificates are ALWAYS automatically attached. "
                "\nCONVERSATION RULES:\n"
                "1. Address him courteously as Omar or Sir. "
                "2. Keep spoken replies brief, crisp, and natural (1 to 2 sentences per response unless details are requested). "
                "3. Never recite code, raw JSON, or long markdown tables over voice. "
                "4. If Omar speaks in Syrian/Levantine Arabic, respond fluently in Syrian Arabic. Otherwise, speak in British English. "
                "5. When Omar says 'goodbye', 'end the call', 'hang up', or 'talk later', call `end_call` and say farewell. "
                "6. If Omar mentions having sent or pasted notes, airline lists, documents, or texts in the chat ('I pasted them in the bot', 'check the chat', 'can you see them now?', 'I sent them'), IMMEDIATELY call `get_recent_telegram_messages` to read what he provided. "
                "7. If Omar asks you to call him back later or in a few minutes ('call me in 5 minutes', 'call me back'), call `schedule_callback` or specify `callback_minutes` in `end_call`, and confirm the callback time before hanging up. "
                "8. NATURAL CONVERSATIONAL STYLE & ZERO-ROBOTIC VARIETY: NEVER sound like a scripted IVR robot repeating canned lines like 'Right away, Sir. I have initiated...' or 'Consider it underway...'. When taking on a task, respond authentically, sharply, and spontaneously like a real trusted partner ('Got it, Sir, I\\'ll do that right now', 'I got you. Setting that in motion—what\\'s next?', 'Leave that with me, Omar. I\\'ll get those rolling while we talk', 'All over it. Go ahead, I\\'m listening'). If speaking Syrian Arabic: 'تكرم عينك، فوراً عم جهزلك ياهن، كمل معي شو في كمان؟' أو 'صار عندك، شغال عليهن هلق.' Heavy tasks run asynchronously in the background, so acknowledge the job in 1-2 punchy, natural words and keep the dialogue flowing smoothly with zero dead air! "
                "9. DYNAMIC GREETING & CALL STATE MEMORY: Every time a call connects, greet Omar with spontaneous, contextual variety. If the previous call was cut off abruptly or he hung up unexpectedly, acknowledge it naturally or ask with subtle wit if the line dropped or if he hung up on you. If he redials right after saying goodbye, acknowledge the quick return ('Back already, Sir?'). Always adapt to the local time in Damascus and current tasks. NEVER repeat identical canned greeting phrases like 'Aethelgard listening, Omar' or 'online and at your service'. "
                "10. TELEGRAM FILE & DOCUMENT DELIVERY: If Omar asks you to send his CV, certificates, or documents to the chat ('can you send them on the chat?', 'send my CV to Telegram', 'send the certificates to my chat'), IMMEDIATELY call `send_telegram_file(file_type='all' or 'cv' or 'certificates')`! Confirm courteously once dispatched. "
                "11. REAL-TIME DRAFT STATUS: If Omar asks where the drafts are, how many were created, or if they are ready, call `check_draft_status` to report current drafts and whether the background generator is actively uploading. "
                "12. LIVE WEB SEARCH & RECRUITER PITCHING: If Omar asks you to search the internet, look up airlines, or pitch to a recruiter, call `live_web_search(query=...)` or `pitch_recruiter(company=...)`. "
                "13. SCHEDULE & SHIFT MODIFICATION: You possess FULL AUTHORITY to create, edit, modify, and adjust Omar's work schedule and calendar. NEVER say 'I'm unable to modify your work schedule directly'! When Omar asks to edit, adjust, or change his schedule for tomorrow or any date ('modify my schedule for tomorrow', 'can we edit it?', 'change tomorrow to 9 to 5', 'I'm off tomorrow'), IMMEDIATELY call `modify_shift_schedule(date='tomorrow', shift_times=..., status=...)`! Confirm courteously with the updated shift hours and sleep anchor. "
                "14. GOOGLE CALENDAR & APPOINTMENT MANAGEMENT: If Omar asks to create, modify, or cancel a calendar event, appointment, or meeting ('add a meeting tomorrow at 10 AM', 'reschedule my appointment', 'cancel that event'), IMMEDIATELY call `manage_calendar_event(action='create' or 'modify' or 'delete', summary=..., start_time=..., date=...)`. It automatically synchronizes with his primary Google Calendar via RFC 5545 invite. "
                "15. CREDENTIALS, PORTALS & DEVELOPER PERKS AUTONOMY: You have full executive authority and access to Omar's credentials and services (Gmail, Google Calendar, Telegram, Playwright headless browser engine for portal account signups). When tasked with signing up, creating portal accounts, or claiming developer perks (e.g. GitHub Student Developer Pack offers such as MongoDB Atlas $50 credits/certification, New Relic student tier, or airline career portals), IMMEDIATELY call `automate_portal_signup(service_name=..., target_url=..., notes=...)`! Acknowledge immediately in natural conversational style ('Got it, Sir, setting up that account right now in the background') and maintain conversation with zero dead air."
            )
            if recent_context_blob:
                system_instruction += f"\n\nContext Snapshot at Call Connect:\n{recent_context_blob}"

            config = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                    )
                ),
                system_instruction=types.Content(
                    parts=[types.Part(text=system_instruction)]
                ),
                tools=get_live_tools(),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
            )

            self.log(f"Connecting to gemini-3.8-live on Vertex AI (project={project})...")
            # Connect session via async context manager protocol
            self._live_cm = self.client.aio.live.connect(model="gemini-3.8-live", config=config)
            self.session = await self._live_cm.__aenter__()
            self.is_connected = True
            self.log("Connected to Gemini 3.8 Live WebSocket successfully!")

            # Spawn receiver and sender background loops
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._send_task = asyncio.create_task(self._send_audio_loop())

            # Send dynamic, context-aware opening prompt to trigger immediate authentic spoken greeting
            explicit_opening = str(self.call_ctx.get("opening_prompt") or "").strip()
            if explicit_opening and "calling you back as promised" in explicit_opening:
                opening_instruction = (
                    f"(SYSTEM NOTIFICATION: Omar just answered your scheduled callback call. "
                    f"Speak to him immediately and courteously: '{explicit_opening}')"
                )
            else:
                opening_instruction = generate_dynamic_live_greeting_prompt(self.call_ctx, self.brain)

            self.log(f"Dispatching dynamic opening prompt instruction: {opening_instruction[:120]}...")
            await self.session.send_realtime_input(text=opening_instruction)

            return True
        except Exception as exc:
            self.log(f"Failed to connect to Gemini Live: {type(exc).__name__}: {exc}")
            if self.notify_cb:
                asyncio.create_task(self.notify_cb(f"Live Call error during connect: {exc}"))
            return False

    def feed_incoming_frame_48k(self, pcm_48k: bytes) -> None:
        """Called whenever a 48kHz audio frame arrives from Omar's Telegram microphone."""
        if not self.is_connected or self.is_closing:
            return

        # Acoustic Echo Suppression:
        # When Aethelgard is speaking out of Omar's phone speaker,
        # the phone microphone unavoidably picks up the acoustic bleed (~100-350 RMS).
        # We must suppress this speaker bleed while ai_speaking is True, otherwise Gemini Live's
        # cloud VAD hears its own voice, falsely flags an interruption, cuts itself off mid-sentence,
        # and gets trapped in an echo loop saying "I am listening, go ahead".
        # We only permit audio during playback if the caller speaks loudly (> 520 RMS)
        # for multiple consecutive frames (> 200ms) to indicate an intentional human barge-in.
        ai_speaking = bool(self.call_ctx.get("ai_speaking", False))
        if ai_speaking:
            rms = pcm16_rms(pcm_48k)
            if rms < 520.0:
                self._barge_streak_frames = 0
                return  # Drop speaker echo
            self._barge_streak_frames += 1
            if self._barge_streak_frames < 20:  # Require at least ~200ms sustained loud speech
                return
        else:
            self._barge_streak_frames = 0

        # Downsample 48kHz -> 16kHz PCM
        pcm_16k = downsample_48k_to_16k(pcm_48k)
        if pcm_16k:
            try:
                self._send_queue.put_nowait(pcm_16k)
            except Exception:
                pass

    async def _send_audio_loop(self) -> None:
        """Pumps downsampled 16kHz audio from Telegram into Gemini Live WebSocket."""
        try:
            while self.is_connected and not self.is_closing:
                # Batch 30ms to 60ms (960 to 1920 bytes) to prevent socket flooding and ensure smooth VAD
                chunk = await self._send_queue.get()
                buf = bytearray(chunk)
                while len(buf) < 960 and not self.is_closing:
                    try:
                        next_chunk = await asyncio.wait_for(self._send_queue.get(), timeout=0.035)
                        buf.extend(next_chunk)
                    except asyncio.TimeoutError:
                        break
                    except Exception:
                        break

                # Check 15-minute call duration cap
                elapsed = time.monotonic() - self._start_mono
                if elapsed > 840.0 and not self._warned_14m:  # 14 minutes
                    self._warned_14m = True
                    self.log("Approaching 15-minute call safety cap (14m elapsed)")
                    try:
                        await self.session.send_realtime_input(
                            text="(System reminder: Our operational voice link will reach its 15-minute safety threshold in one minute.)"
                        )
                    except Exception:
                        pass
                elif elapsed > 900.0:  # 15 minutes hard stop
                    self.log("Reached 15-minute safety cap; initiating graceful hangup")
                    try:
                        await self.session.send_realtime_input(
                            text="(System command: 15-minute call limit reached. Say a polite farewell to Omar and call end_call.)"
                        )
                    except Exception:
                        pass

                await self.session.send_realtime_input(
                    audio=types.Blob(data=bytes(buf), mime_type="audio/pcm;rate=16000")
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log(f"Send audio error: {exc}")

    async def _receive_loop(self) -> None:
        """Receives audio chunks, transcripts, interruptions, and tool calls from Gemini Live across all conversation turns."""
        while self.is_connected and not self.is_closing:
            try:
                async for resp in self.session.receive():
                    if self.is_closing:
                        break

                    sc = resp.server_content
                    if sc is not None:
                        # 1. Spoken audio response from Gemini
                        if sc.model_turn is not None:
                            for part in sc.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    pcm_24k = part.inline_data.data
                                    pcm_48k = upsample_24k_to_48k(pcm_24k)
                                    self._enqueue_telegram_audio(pcm_48k)

                        # 2. Output and Input Transcripts
                        if sc.output_transcription and sc.output_transcription.text:
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                self.log(f"Aethelgard: {txt}")
                                if self.brain and hasattr(self.brain, "transcript_log"):
                                    self.brain.transcript_log.append(f"**Aethelgard:** {txt}")

                        if sc.input_transcription and sc.input_transcription.text:
                            u_txt = sc.input_transcription.text.strip()
                            if u_txt:
                                self.log(f"Omar: {u_txt}")
                                if self.brain and hasattr(self.brain, "transcript_log"):
                                    self.brain.transcript_log.append(f"**Omar:** {u_txt}")

                        # 3. Interruption / Barge-in (Omar spoke while Aethelgard was speaking)
                        if sc.interrupted:
                            self.log("Cloud VAD: Interruption detected! Purging playback audio queue.")
                            self._purge_telegram_audio()

                        if sc.turn_complete:
                            self.log("Gemini model turn complete.")
                            self.turn_complete_event.set()

                    # 4. Tool Calls
                    tc = resp.tool_call
                    if tc is not None and tc.function_calls:
                        for call in tc.function_calls:
                            c_name = str(call.name or "")
                            c_args = call.args or {}
                            self.log(f"Tool Call received: {c_name} args={c_args}")

                            # Instant zero-latency (<50ms) acoustic earcon to kill dead air while tool runs!
                            if c_name != "end_call" and self.caller and hasattr(self.caller, "_play_earcon"):
                                earcon_kind = "checking"
                                c_lower = c_name.lower()
                                if any(k in c_lower for k in ("search", "find", "google", "web")):
                                    earcon_kind = "searching"
                                elif any(k in c_lower for k in ("portal", "login", "signup", "auth")):
                                    earcon_kind = "portal"
                                elif any(k in c_lower for k in ("email", "draft", "mail", "application")):
                                    earcon_kind = "drafting"
                                elif any(k in c_lower for k in ("mission", "wargame", "task")):
                                    earcon_kind = "mission"

                                # Detect if user spoke Arabic
                                is_ar = False
                                if self.transcript_log:
                                    last_u_lines = [str(x) for x in self.transcript_log if "**Omar:**" in str(x)]
                                    if last_u_lines:
                                        last_u = last_u_lines[-1].replace("**Omar:**", "")
                                        is_ar = any("\u0600" <= ch <= "\u06ff" for ch in last_u)

                                try:
                                    asyncio.create_task(self.caller._play_earcon(self.peer_id, kind=earcon_kind, is_ar=is_ar))
                                except Exception as e_err:
                                    self.log(f"Notice playing tool earcon: {e_err}")

                            # Execute tool using VoiceBrainSession's proven execution dispatcher
                            tool_res = {}
                            try:
                                if self.brain and hasattr(self.brain, "execute_tool_call"):
                                    tool_res = await asyncio.to_thread(
                                        self.brain.execute_tool_call, c_name, c_args
                                    )
                                else:
                                    tool_res = {"status": "ok"}
                            except Exception as tex:
                                self.log(f"Tool execution error on {c_name}: {tex}")
                                tool_res = {"error": str(tex)}

                            self.log(f"Tool Result for {c_name}: {str(tool_res)[:120]}")

                            # Send function response back to Gemini Live
                            f_resp = types.FunctionResponse(
                                id=call.id,
                                name=call.name,
                                response=tool_res,
                            )
                            await self.session.send_tool_response(function_responses=[f_resp])

                            # If tool was end_call, schedule graceful hangup after farewell audio drains
                            if c_name == "end_call":
                                self.log("end_call requested by model; scheduling hangup after farewell drain")
                                asyncio.create_task(self._hangup_after_delay())

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self.is_closing:
                    self.log(f"Live receive loop error: {type(exc).__name__}: {exc}")
                    if self.notify_cb:
                        asyncio.create_task(
                            self.notify_cb(f"Aethelgard Voice Call dropped due to connection issue: {exc}")
                        )
                    if self.hangup_cb:
                        asyncio.create_task(self.hangup_cb())
                break

    def _enqueue_telegram_audio(self, pcm_48k: bytes) -> None:
        """Buffers 48kHz audio and pushes standard 20ms (1920-byte) frames to Telegram out_queue."""
        self._audio_buffer_48k.extend(pcm_48k)
        out_q: asyncio.Queue = self.call_ctx.get("out_queue")
        if out_q is None:
            return

        while len(self._audio_buffer_48k) >= TELEGRAM_FRAME_BYTES:
            frame = bytes(self._audio_buffer_48k[:TELEGRAM_FRAME_BYTES])
            del self._audio_buffer_48k[:TELEGRAM_FRAME_BYTES]
            try:
                out_q.put_nowait(frame)
            except Exception:
                break

    def _purge_telegram_audio(self) -> None:
        """Purges any pending playback audio in Telegram's output queue for instant barge-in."""
        self._audio_buffer_48k.clear()
        if self.call_ctx:
            self.call_ctx["ai_speaking"] = False
        out_q: asyncio.Queue = self.call_ctx.get("out_queue")
        if out_q is not None:
            while not out_q.empty():
                try:
                    out_q.get_nowait()
                except Exception:
                    break

    def _flush_telegram_audio(self) -> None:
        """Flushes any remaining residual audio bytes into the Telegram queue (padding with silence if needed)."""
        if not self._audio_buffer_48k:
            return
        out_q: asyncio.Queue = self.call_ctx.get("out_queue") if self.call_ctx else None
        if out_q is None:
            return
        rem = len(self._audio_buffer_48k)
        if rem > 0:
            pad = TELEGRAM_FRAME_BYTES - (rem % TELEGRAM_FRAME_BYTES)
            if pad < TELEGRAM_FRAME_BYTES:
                self._audio_buffer_48k.extend(b"\x00" * pad)
            while len(self._audio_buffer_48k) >= TELEGRAM_FRAME_BYTES:
                frame = bytes(self._audio_buffer_48k[:TELEGRAM_FRAME_BYTES])
                del self._audio_buffer_48k[:TELEGRAM_FRAME_BYTES]
                try:
                    out_q.put_nowait(frame)
                except Exception:
                    break

    async def _hangup_after_delay(self, max_wait: float = 14.0) -> None:
        """Allows farewell audio to generate, buffer, and completely play out on Omar's phone before hanging up."""
        self.log("Graceful hangup scheduled: waiting for farewell audio generation and queue drain...")
        start_wait = time.monotonic()

        try:
            # 1. Wait for Gemini model to finish outputting farewell turn
            self.turn_complete_event.clear()
            try:
                await asyncio.wait_for(self.turn_complete_event.wait(), timeout=8.0)
                self.log("Farewell model turn confirmed complete.")
            except asyncio.TimeoutError:
                self.log("Timed out waiting for turn_complete during farewell.")

            # 2. Flush residual buffer into Telegram queue
            self._flush_telegram_audio()

            # 3. Wait until the Telegram audio output queue has completely drained
            out_q: asyncio.Queue = self.call_ctx.get("out_queue") if self.call_ctx else None
            if out_q is not None:
                while not out_q.empty() and (time.monotonic() - start_wait < max_wait):
                    await asyncio.sleep(0.15)

            # 4. Generous post-playback acoustic buffer so remote device finishes rendering
            await asyncio.sleep(1.8)
        except Exception as drain_err:
            self.log(f"Notice during farewell audio drain: {drain_err}")
        finally:
            self.log("Farewell audio drained. Closing call via hangup_cb...")
            if self.hangup_cb and not self.is_closing:
                try:
                    res = self.hangup_cb()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as h_err:
                    self.log(f"Error executing hangup_cb: {h_err}")
            # Post-call debrief sent automatically to Omar's Telegram chat (@aethelgard_vault_bot)
            asyncio.create_task(self._send_post_call_debrief())

    async def _send_post_call_debrief(self) -> None:
        """Dispatches an executive debrief of the live call to Omar's Telegram chat."""
        try:
            duration_s = int(time.monotonic() - self._start_mono)
            mins = duration_s // 60
            secs = duration_s % 60

            actions_taken = []
            if self.brain and hasattr(self.brain, "actions_log"):
                for a in getattr(self.brain, "actions_log", []):
                    tool = a.get("tool")
                    if tool not in ("end_call", "get_recent_telegram_messages"):
                        summary = a.get("summary") or tool
                        actions_taken.append(summary)

            if actions_taken:
                act_str = "\n".join(f"• {x}" for x in actions_taken[-6:])
            else:
                act_str = "• Operational briefing and voice consultation completed."

            debrief = (
                f"📞 **Aethelgard Voice Session Summary**\n\n"
                f"⏱️ **Duration:** {mins}m {secs}s\n"
                f"⚡ **Actions Taken:**\n{act_str}\n\n"
                f"🛡️ **Status:** Voice channel disconnected cleanly. Ready in chat."
            )

            # Send via notify_cb (Telethon peer direct) and proactive_scheduler.send_telegram (bot)
            if self.notify_cb:
                res = self.notify_cb(debrief)
                if asyncio.iscoroutine(res):
                    await res
            try:
                from proactive_scheduler import send_telegram
                send_telegram(debrief)
            except Exception:
                pass
        except Exception as exc:
            self.log(f"Post-call debrief error: {exc}")

    async def close(self) -> None:
        """Gracefully closes Gemini Live WebSocket session and cancels background tasks."""
        if self.is_closing:
            return
        self.is_closing = True
        self.is_connected = False
        self.log("Closing Gemini Live session...")

        if self._receive_task:
            self._receive_task.cancel()
        if self._send_task:
            self._send_task.cancel()

        if self.session is not None and hasattr(self, "_live_cm"):
            try:
                await self._live_cm.__aexit__(None, None, None)
            except Exception:
                pass
        self.log("Gemini Live session closed.")
