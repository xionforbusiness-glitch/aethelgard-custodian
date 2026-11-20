#!/usr/bin/env python3
"""
voice_biometrics.py — Ultra-Low Latency Acoustic Voice Biometrics
================================================================
Extracts 512-dimensional speaker embeddings using a lightweight CAM++ ONNX model
to perform acoustic speaker verification without introducing any audio latency.

Key Capabilities:
  - Background asynchronous voiceprint extraction (<30ms inference).
  - Cosine similarity matching against enrolled voiceprints.
  - Multi-tier persistence: MongoDB Atlas + resilient local JSON cache.
  - Calibration mode for instant master enrollment.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("Aethelgard.VoiceBiometrics")

# Path to CAM++ ONNX model
MODEL_DIR = Path(os.path.expanduser("~/.aethelgard/models/speaker_embedding"))
MODEL_PATH = MODEL_DIR / "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"

STATE_DIR = Path(os.path.expanduser("~/.aethelgard/state"))
VOICEPRINTS_CACHE_FILE = STATE_DIR / "speaker_voiceprints.json"

_extractor = None
_extractor_init_attempted = False

# Verification thresholds
VERIFICATION_THRESHOLD = 0.68  # Above 0.68 -> confirmed Omar
SECURITY_CHALLENGE_THRESHOLD = 0.58  # Below 0.58 -> confirmed mismatch (Bodyguard mode)


def get_extractor():
    """Initializes and caches the Sherpa-ONNX SpeakerEmbeddingExtractor."""
    global _extractor, _extractor_init_attempted
    if _extractor is not None:
        return _extractor
    if _extractor_init_attempted:
        return None

    _extractor_init_attempted = True
    if not MODEL_PATH.exists():
        logger.warning(f"Speaker embedding model not found at {MODEL_PATH}")
        return None

    try:
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(MODEL_PATH),
            num_threads=2,
            debug=False,
            provider="cpu",
        )
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        logger.info(f"Loaded SpeakerEmbeddingExtractor (dim={_extractor.dim})")
        return _extractor
    except Exception as e:
        logger.error(f"Failed to initialize SpeakerEmbeddingExtractor: {e}")
        return None


def _load_cached_voiceprints() -> Dict[str, List[float]]:
    """Loads enrolled voiceprints from local JSON cache."""
    if not VOICEPRINTS_CACHE_FILE.exists():
        return {}
    try:
        with open(VOICEPRINTS_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to read local voiceprints cache: {e}")
        return {}


def _save_cached_voiceprints(voiceprints: Dict[str, List[float]]) -> None:
    """Saves enrolled voiceprints to local JSON cache."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(VOICEPRINTS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(voiceprints, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write local voiceprints cache: {e}")


def get_enrolled_voiceprint(speaker_name: str = "omar") -> Optional[np.ndarray]:
    """Retrieves the enrolled voiceprint vector from MongoDB or local cache."""
    clean_name = speaker_name.lower().strip()

    # 1. Check local cache
    cached = _load_cached_voiceprints()
    if clean_name in cached:
        return np.array(cached[clean_name], dtype=np.float32)

    # 2. Check MongoDB Atlas
    try:
        import mongo_vault
        db = mongo_vault.get_db()
        if db is not None:
            doc = db.speaker_voiceprints.find_one({"name": clean_name})
            if doc and "embedding" in doc:
                emb = doc["embedding"]
                cached[clean_name] = emb
                _save_cached_voiceprints(cached)
                return np.array(emb, dtype=np.float32)
    except Exception as e:
        logger.warning(f"Failed to fetch voiceprint from MongoDB: {e}")

    return None


def compute_embedding(pcm_bytes: bytes, sample_rate: int = 16000) -> Optional[np.ndarray]:
    """
    Extracts a 512-dim embedding from raw 16-bit mono PCM audio bytes.
    Runs in ~25ms on modern CPU.
    """
    extractor = get_extractor()
    if extractor is None:
        return None

    if len(pcm_bytes) < 16000 * 2 * 0.8:  # Minimum ~0.8s of audio
        logger.debug(f"PCM audio too short for embedding ({len(pcm_bytes)} bytes)")
        return None

    try:
        # Convert 16-bit PCM bytes to float32 normalized in [-1.0, 1.0]
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
        stream.input_finished()

        emb = extractor.compute(stream)
        if not emb:
            return None

        v = np.array(emb, dtype=np.float32)
        norm = np.linalg.norm(v)
        if norm > 1e-6:
            v = v / norm  # L2-normalize
        return v
    except Exception as e:
        logger.error(f"Error computing speaker embedding: {e}")
        return None


def enroll_speaker(name: str, pcm_bytes: bytes, sample_rate: int = 16000) -> Dict[str, Any]:
    """
    Enrolls a speaker voiceprint from raw PCM audio bytes.
    Persists to both MongoDB Atlas and local disk cache.
    """
    clean_name = name.lower().strip()
    emb = compute_embedding(pcm_bytes, sample_rate=sample_rate)
    if emb is None:
        return {"success": False, "error": "Insufficient or invalid audio for enrollment"}

    emb_list = emb.tolist()

    # 1. Save local
    cached = _load_cached_voiceprints()
    cached[clean_name] = emb_list
    _save_cached_voiceprints(cached)

    # 2. Save MongoDB
    try:
        import mongo_vault
        db = mongo_vault.get_db()
        if db is not None:
            db.speaker_voiceprints.update_one(
                {"name": clean_name},
                {
                    "$set": {
                        "name": clean_name,
                        "embedding": emb_list,
                        "updated_at": time.time(),
                    }
                },
                upsert=True,
            )
    except Exception as e:
        logger.warning(f"Could not persist voiceprint to MongoDB: {e}")

    logger.info(f"✅ Successfully enrolled voiceprint for speaker '{clean_name}' (dim={len(emb_list)})")
    return {"success": True, "speaker": clean_name, "dim": len(emb_list)}


def verify_speaker(
    pcm_bytes: bytes,
    target_speaker: str = "omar",
    sample_rate: int = 16000,
) -> Dict[str, Any]:
    """
    Compares incoming PCM audio against the enrolled voiceprint for target_speaker.
    Returns similarity score (0.0 to 1.0) and verification decision.
    """
    enrolled = get_enrolled_voiceprint(target_speaker)
    if enrolled is None:
        return {
            "evaluated": False,
            "reason": f"No enrolled voiceprint found for '{target_speaker}'",
            "is_verified": True,  # Graceful fallback: don't lock out if not enrolled yet
            "similarity": 1.0,
            "security_challenge_needed": False,
        }

    emb = compute_embedding(pcm_bytes, sample_rate=sample_rate)
    if emb is None:
        return {
            "evaluated": False,
            "reason": "Audio too short or silent",
            "is_verified": True,
            "similarity": 1.0,
            "security_challenge_needed": False,
        }

    # Cosine similarity between normalized vectors is dot product
    sim = float(np.dot(emb, enrolled))
    is_verified = sim >= VERIFICATION_THRESHOLD
    challenge = sim < SECURITY_CHALLENGE_THRESHOLD

    return {
        "evaluated": True,
        "speaker": target_speaker,
        "similarity": round(sim, 4),
        "is_verified": is_verified,
        "security_challenge_needed": challenge,
    }
