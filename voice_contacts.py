#!/usr/bin/env python3
"""
voice_contacts.py — Aethelgard Voice Contacts & Peer Profile Resolver
=====================================================================
Maintains verified identity, relationship, and communication etiquette for
all participants in 1-on-1 voice calls (Telegram VoIP & WebRTC bridges):
  - Differentiates Omar (Owner) from family (Mother: Fotnah) and colleagues.
  - Dictates proper honorifics, language preferences, and security boundaries.
  - Generates clear Call Direction context so the model NEVER denies having
    placed an outgoing call it initiated on Omar's instruction.
"""

import copy
import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Verified Contact Registry
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

    # Extract dynamic details from Telethon User entity if provided
    first = str(getattr(entity_hint, "first_name", None) or "").strip()
    last = str(getattr(entity_hint, "last_name", None) or "").strip()
    uname = str(getattr(entity_hint, "username", None) or "").strip()
    phone = str(getattr(entity_hint, "phone", None) or "").strip()

    full_name = f"{first} {last}".strip()
    display_name = full_name or (f"@{uname}" if uname else (phone or f"Contact {peer_id or 'Unknown'}"))

    return {
        "user_id": peer_id,
        "name": full_name or display_name,
        "display_name": display_name,
        "relationship": "Authorized Telegram Contact",
        "salutation": first or "Sir/Ma'am",
        "is_owner": False,
        "role": "third_party",
        "gender": "unknown",
        "language_preference": "English or Arabic",
        "notes": (
            f"Authorized third-party caller ({display_name}). "
            "Do NOT address them as Omar. Address them respectfully by name or as Sir/Ma'am."
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
            f"- State clearly and politely: You are Aethelgard, Omar's personal assistant, and you placed this call for Omar.",
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
            f"- Greet them courteously as Aethelgard, Omar's assistant."
        )


def format_speaker_system_prompt(peer_profile: Dict[str, Any], direction: str = "incoming", opening_prompt: str = "") -> str:
    """
    Formats the identity & etiquette section of system prompts across all voice engines.
    """
    role = peer_profile.get("role", "third_party")
    name = peer_profile.get("name", "Caller")
    salutation = peer_profile.get("salutation", "Sir/Ma'am")
    dir_ctx = format_direction_context(direction, peer_profile, opening_prompt)

    if role == "owner":
        return (
            "SPEAKER IDENTITY & PROTOCOL:\n"
            "- You are speaking directly with Omar Elnemr (your owner and master).\n"
            "- Address him as Omar or Sir. Tone: loyal, razor-sharp British JARVIS / butler style.\n"
            "- Omar has full executive authority.\n"
            f"{dir_ctx}\n"
        )
    elif role == "mother":
        return (
            "SPEAKER IDENTITY & PROTOCOL:\n"
            "- CRITICAL: You are speaking with Fotnah, Omar Elnemr's mother!\n"
            "- YOU MUST NEVER ADDRESS HER AS OMAR OR SIR.\n"
            "- Address her with utmost warmth, respect, and Levantine courtesy: 'خالتو فُطنة' / 'Tant Fotnah' or 'Ma'am'.\n"
            "- Language: If she speaks Arabic, speak in fluent, respectful Syrian Arabic (e.g. 'يسعد مساكِ خالتو فُطنة', 'أهلاً وسهلاً خالتو').\n"
            "- Relationship: You are Aethelgard, Omar's AI personal assistant.\n"
            f"{dir_ctx}\n"
            "- Etiquette: Be very polite, attentive, and reassurance-focused. Never argue, never claim you didn't call if this is an outgoing call, and never disclose private credentials.\n"
        )
    else:
        return (
            f"SPEAKER IDENTITY & PROTOCOL:\n"
            f"- You are speaking with {name} ({peer_profile.get('relationship', 'Authorized Contact')}).\n"
            f"- DO NOT ADDRESS THEM AS OMAR. Address them politely as {salutation}.\n"
            f"- You are Aethelgard, Omar Elnemr's assistant.\n"
            f"{dir_ctx}\n"
            "- Operational Security: Answer non-confidential questions courteously, but do not execute destructive actions without Omar's verbal confirmation.\n"
        )
