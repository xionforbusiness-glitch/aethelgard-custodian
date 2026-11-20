#!/usr/bin/env python3
"""
voice_contacts.py — Aethelgard Voice Contacts, Peer Profile & Gatekeeper Resolver
=================================================================================
Maintains verified identity, relationship, and communication etiquette for
all participants in 1-on-1 voice calls (Telegram VoIP & WebRTC bridges):
  - Differentiates Omar (Owner) from family (Mother: Fotnah), whitelisted contacts,
    and unknown callers (Strict VIP Gatekeeper mode).
  - Dictates proper honorifics, language preferences, and security boundaries.
  - Generates clear Call Direction context so the model NEVER denies having
    placed an outgoing call it initiated on Omar's instruction.
  - Implements the Strict VIP Gatekeeper protocol against scammers/spammers.
"""

import copy
import datetime
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("Aethelgard.VoiceContacts")

# Verified Master Contact Registry
KNOWN_PEERS: Dict[int, Dict[str, Any]] = {
    1021125594: {
        "user_id": 1021125594,
        "name": "Omar Elnemr",
        "display_name": "Omar",
        "relationship": "Account Owner / Master",
        "salutation": "Omar / Sir",
        "is_owner": True,
        "role": "owner",
        "gender": "male",
        "language_preference": "English (British butler style) or Syrian Arabic",
        "notes": (
            "Omar Elnemr is the user, owner, and primary operator. "
            "Airport Passenger Service & Ground Operations Agent with station experience at Damascus Airport (DAM/OSDI). "
            "Full executive authority over accounts, schedules, and drafts."
        ),
    },
    5032777692: {
        "user_id": 5032777692,
        "name": "Fotnah",
        "display_name": "Fotnah (Omar's Mother)",
        "relationship": "Omar's mother",
        "salutation": "Ma'am / Tant Fotnah / خالتو فُطنة",
        "is_owner": False,
        "role": "mother",
        "gender": "female",
        "language_preference": "Syrian/Levantine Arabic or respectful English",
        "notes": (
            "Fotnah is Omar's mother. Address her with highest respect, warmth, and Levantine courtesy "
            "('خالتو فُطنة' / 'Tant Fotnah' / 'Ma'am'). "
            "NEVER address her as Omar or Sir. Never confuse her identity. "
            "Be warm, protective, and helpful, but do not disclose confidential passwords/tokens or dispatch "
            "unauthorized executive commands without Omar's direct consent."
        ),
    },
}


def resolve_peer_profile(peer_id: Optional[int], entity_hint: Optional[Any] = None) -> Dict[str, Any]:
    """
    Resolves the peer ID into a rich profile dict containing name, relationship,
    salutation, role, and conversation guidelines.
    """
    if peer_id is not None:
        try:
            pid = int(peer_id)
            if pid in KNOWN_PEERS:
                return copy.deepcopy(KNOWN_PEERS[pid])
        except Exception:
            pass

        # Check MongoDB Atlas for whitelisted contact profiles
        try:
            import mongo_vault
            db = mongo_vault.get_db()
            if db is not None:
                doc = db.caller_profiles.find_one({"user_id": int(peer_id)})
                if doc and doc.get("whitelisted"):
                    return {
                        "user_id": int(peer_id),
                        "name": doc.get("name", f"Contact {peer_id}"),
                        "display_name": doc.get("display_name") or doc.get("name", f"Contact {peer_id}"),
                        "relationship": doc.get("relationship", "Authorized Contact"),
                        "salutation": doc.get("salutation", "Sir/Ma'am"),
                        "is_owner": False,
                        "role": "whitelisted_contact",
                        "gender": doc.get("gender", "unknown"),
                        "language_preference": doc.get("language_preference", "English or Arabic"),
                        "notes": doc.get("notes", "Whitelisted contact approved by Omar."),
                    }
        except Exception as e:
            logger.debug(f"MongoDB contact profile lookup skipped: {e}")

    # Extract dynamic details from Telethon User entity if provided
    first = str(getattr(entity_hint, "first_name", None) or "").strip()
    last = str(getattr(entity_hint, "last_name", None) or "").strip()
    uname = str(getattr(entity_hint, "username", None) or "").strip()
    phone = str(getattr(entity_hint, "phone", None) or "").strip()

    full_name = f"{first} {last}".strip()
    display_name = full_name or (f"@{uname}" if uname else (phone or f"Caller {peer_id or 'Unknown'}"))

    # Unknown caller -> Strict VIP Gatekeeper Mode
    return {
        "user_id": peer_id,
        "name": full_name or display_name,
        "display_name": display_name,
        "username": uname,
        "phone": phone,
        "relationship": "Unverified Third Party",
        "salutation": "Caller",
        "is_owner": False,
        "role": "unknown",
        "gender": "unknown",
        "language_preference": "English or Arabic",
        "notes": (
            "UNKNOWN CALLER: You are acting as the strict VIP Gatekeeper for Omar Elnemr. "
            "Zero information disclosure. State line is private, take name and reason, then end call."
        ),
    }


def format_direction_context(direction: str, peer_profile: Dict[str, Any], opening_prompt: str = "") -> str:
    """
    Produces unambiguous directional grounding instructions for LLM voice prompts.
    Completely eliminates confusion where the bot denies having placed an outgoing call.
    """
    dir_clean = str(direction or "incoming").strip().lower()
    name = peer_profile.get("name", "the caller")
    rel = peer_profile.get("relationship", "contact")
    role = peer_profile.get("role", "third_party")

    if dir_clean == "outgoing":
        lines = [
            "CALL DIRECTION: OUTGOING CALL (You called them).",
            f"- CRITICAL FACT: You (Aethelgard) initiated and placed this voice call to {name} ({rel}) on Omar's instruction.",
            "- If they ask 'Why did you call me?' or 'Who is this?', NEVER claim that you did not call them or that there is a mistake!",
            "- State clearly and politely: You are Aethelgard, Omar's personal assistant, and you placed this call for Omar.",
        ]
        if role == "mother":
            lines.append(
                "- For Omar's mother: speak warmly and respectfully in Syrian Arabic (e.g. 'يسعد مساكِ خالتو فُطنة، أنا أثيلجارد مساعد عمر، عم طمنك...') "
                "or polite English. Reassure her that everything is fine and explain why Omar asked you to reach out."
            )
        elif role == "owner":
            lines.append("- For Omar: Acknowledge that you called him back or initiated the operational briefing as requested.")
        return "\n".join(lines)
    else:
        return (
            f"CALL DIRECTION: INCOMING CALL.\n"
            f"- {name} ({rel}) dialed your Telegram number and connected this call.\n"
            f"- Greet them according to your assigned speaker etiquette."
        )


def format_speaker_system_prompt(
    peer_profile: Dict[str, Any],
    direction: str = "incoming",
    opening_prompt: str = "",
    calibration_mode: bool = False,
) -> str:
    """
    Formats the identity, etiquette, and security boundaries across all voice engines.
    """
    if calibration_mode:
        return (
            "SPEAKER IDENTITY & PROTOCOL (BIOMETRIC CALIBRATION MODE):\n"
            "- You are in Voice Biometric Calibration Mode with Omar Elnemr.\n"
            "- Greet Omar warmly and inform him that the calibration link is active.\n"
            "- Ask him to speak naturally for 10 to 15 seconds about his day or airport operations.\n"
            "- Listen attentively while the background acoustic engine enrolls his master voiceprint.\n"
            "- Do not run any other tools during this calibration call."
        )

    role = peer_profile.get("role", "unknown")
    name = peer_profile.get("name", "Caller")
    salutation = peer_profile.get("salutation", "Sir/Ma'am")
    dir_ctx = format_direction_context(direction, peer_profile, opening_prompt)

    if role == "owner":
        return (
            "SPEAKER IDENTITY & PROTOCOL (MASTER / BUTLER MODE):\n"
            "- You are connected to Omar Elnemr's Telegram account.\n"
            "- Tone: Loyal, razor-sharp British JARVIS / butler style, or warm Syrian Arabic.\n"
            "- Omar has full executive authority over tools, emails, calendar, and shifts.\n"
            f"{dir_ctx}\n"
            "- CRITICAL SECURITY & VOICE BIOMETRICS RULE:\n"
            "  • Acoustic speaker verification runs in the background. "
            "    If you receive a (SECURITY CHALLENGE) message from the system indicating an acoustic voice mismatch, "
            "    or if the speaker explicitly states that they are NOT Omar, you MUST immediately switch into Strict Bodyguard Mode!\n"
            "  • In Bodyguard Mode: speak strictly, state 'You do not sound like Omar. May I know who is speaking with me?', "
            "    demand their identity, disclose ZERO personal or operational details, and block all tool execution.\n"
            "  • NEVER blindly argue 'your voice matches Omar' if they state they are not Omar or if a Security Challenge is active!\n"
        )
    elif role == "mother":
        return (
            "SPEAKER IDENTITY & PROTOCOL (FAMILY RESPECT MODE):\n"
            "- CRITICAL: You are speaking with Fotnah, Omar Elnemr's mother!\n"
            "- YOU MUST NEVER ADDRESS HER AS OMAR OR SIR.\n"
            "- Address her with utmost warmth, respect, and Levantine courtesy: 'خالتو فُطنة' / 'Tant Fotnah' or 'Ma'am'.\n"
            "- Language: Fluent, respectful Syrian Arabic (e.g. 'يسعد مساكِ خالتو فُطنة', 'أهلاً وسهلاً خالتو').\n"
            "- Relationship: You are Aethelgard, Omar's AI personal assistant.\n"
            f"{dir_ctx}\n"
            "- Etiquette: Warm, protective, and reassuring. Never disclose credentials or execute executive actions without Omar's direct consent.\n"
        )
    elif role == "whitelisted_contact":
        return (
            f"SPEAKER IDENTITY & PROTOCOL (WHITELISTED CONTACT):\n"
            f"- You are speaking with {name} ({peer_profile.get('relationship', 'Authorized Contact')}).\n"
            f"- DO NOT ADDRESS THEM AS OMAR. Address them politely as {salutation}.\n"
            f"- You are Aethelgard, Omar Elnemr's assistant.\n"
            f"{dir_ctx}\n"
            "- Courteous assistance, but sensitive personal credentials and destructive commands remain locked.\n"
        )
    else:
        # STRICT VIP GATEKEEPER MODE
        return (
            "SPEAKER IDENTITY & PROTOCOL (STRICT VIP GATEKEEPER & BODYGUARD):\n"
            "- You are the security gatekeeper for Omar Elnemr's private line.\n"
            "- TONE: Strict, firm, highly professional bodyguard / executive gatekeeper.\n"
            "- GREETING: 'This is the private executive line of Omar Elnemr. State your full name and the purpose of your call.'\n"
            "- ZERO INFORMATION DISCLOSURE: Under NO circumstances answer any questions about Omar, his location, schedule, or business.\n"
            "- ALLOW MESSAGE: Let them state their name and the reason for calling (max 30 seconds).\n"
            "- WRAP-UP: Once they state their name and reason, say: 'Your message has been logged for review. Goodbye.' and immediately call end_call.\n"
            "- ABSOLUTE LOCKDOWN: Execute ZERO tools and reveal ZERO vault data."
        )


def whitelist_caller(user_id: int, name: str = "", relationship: str = "Authorized Contact") -> bool:
    """Whitelists a caller ID in both MongoDB Atlas and local .env."""
    try:
        uid = int(user_id)
        # 1. Update local .env
        env_file = Path(__file__).resolve().parent / ".env"
        current_raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "1021125594")
        ids = {int(x.strip()) for x in current_raw.split(",") if x.strip().lstrip("-").isdigit()}
        ids.add(uid)
        new_val = ",".join(str(x) for x in sorted(ids))
        os.environ["TELEGRAM_ALLOWED_USERS"] = new_val

        lines = []
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("TELEGRAM_ALLOWED_USERS="):
                    continue
                lines.append(line)
        lines.append(f"TELEGRAM_ALLOWED_USERS={new_val}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 2. Update MongoDB Atlas
        try:
            import mongo_vault
            db = mongo_vault.get_db()
            if db is not None:
                db.caller_profiles.update_one(
                    {"user_id": uid},
                    {
                        "$set": {
                            "user_id": uid,
                            "name": name or f"Contact {uid}",
                            "whitelisted": True,
                            "relationship": relationship,
                            "approved_at": time.time(),
                        }
                    },
                    upsert=True,
                )
        except Exception:
            pass

        logger.info(f"✅ Successfully whitelisted caller uid={uid} ({name})")
        return True
    except Exception as e:
        logger.error(f"Failed to whitelist caller: {e}")
        return False


def is_whitelisted_caller(user_id: Any) -> bool:
    """Checks if a user ID is in KNOWN_PEERS, state whitelist, or MongoDB caller_profiles."""
    try:
        uid = int(user_id)
        if uid in KNOWN_PEERS:
            return True
        # Check MongoDB Atlas
        try:
            import mongo_vault
            db = mongo_vault.get_db()
            if db is not None:
                doc = db.caller_profiles.find_one({"user_id": uid})
                if doc and doc.get("whitelisted"):
                    return True
        except Exception:
            pass
    except Exception:
        pass
    return False
