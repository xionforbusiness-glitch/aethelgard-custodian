#!/usr/bin/env python3
"""
🏛️ AETHELGARD CLOUD DOCUMENT VAULT (MongoDB Atlas Integration)
Provides resilient asynchronous and synchronous persistence for:
- Voice Call Transcripts & Forensics
- Shift Roster & Attendance Schedules
- Autonomous Tool Execution Logs
- Long-term Memory & Knowledge Facts
"""

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger("Aethelgard.MongoVault")

# Try importing pymongo and motor
try:
    import pymongo
    from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False

try:
    import motor.motor_asyncio
    MOTOR_AVAILABLE = True
except ImportError:
    MOTOR_AVAILABLE = False

CREDENTIALS_FILE = Path(os.path.expanduser("~/.aethelgard/credentials/mongo.json"))
DEFAULT_DB_NAME = "aethelgard"


def load_mongo_config() -> Dict[str, Any]:
    """Loads MongoDB configuration from credentials vault or environment variables."""
    cfg = {
        "uri": os.environ.get("MONGODB_URI", ""),
        "database": os.environ.get("MONGODB_DATABASE", DEFAULT_DB_NAME),
    }

    if not cfg["uri"] and CREDENTIALS_FILE.exists():
        try:
            with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg["uri"] = data.get("uri", "")
                cfg["database"] = data.get("database", DEFAULT_DB_NAME)
        except Exception as e:
            logger.warning(f"Failed to read mongo credentials file: {e}")

    return cfg


_sync_client = None
_async_client = None


def get_sync_client(timeout_ms: int = 5000):
    """Returns a cached PyMongo MongoClient instance."""
    global _sync_client
    if not PYMONGO_AVAILABLE:
        logger.error("pymongo is not installed in the active environment.")
        return None

    if _sync_client is not None:
        return _sync_client

    cfg = load_mongo_config()
    if not cfg["uri"]:
        logger.warning("No MongoDB URI configured.")
        return None

    try:
        _sync_client = pymongo.MongoClient(
            cfg["uri"],
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=10000,
        )
        return _sync_client
    except Exception as e:
        logger.error(f"Failed to initialize MongoClient: {e}")
        return None


def get_db():
    """Returns the primary Aethelgard database handle."""
    client = get_sync_client()
    if client is None:
        return None
    cfg = load_mongo_config()
    return client[cfg["database"]]


def test_connection() -> Dict[str, Any]:
    """Tests the Atlas connection and returns health status."""
    if not PYMONGO_AVAILABLE:
        return {"connected": False, "error": "pymongo library not installed"}

    client = get_sync_client(timeout_ms=5000)
    if client is None:
        return {"connected": False, "error": "No URI configured or client creation failed"}

    try:
        # Ping the server
        client.admin.command("ping")
        cfg = load_mongo_config()
        db = client[cfg["database"]]
        collections = db.list_collection_names()
        return {
            "connected": True,
            "database": cfg["database"],
            "collections": collections,
            "server_info": client.server_info().get("version", "unknown")
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


def log_voice_call(call_id: str, summary: str, transcript: str, duration_sec: float = 0.0, meta: Optional[Dict[str, Any]] = None) -> bool:
    """Stores a voice call transcript and analysis in the 'voice_calls' collection."""
    try:
        db = get_db()
        if db is None:
            return False
        
        doc = {
            "call_id": call_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "transcript": transcript,
            "duration_sec": duration_sec,
            "metadata": meta or {},
            "created_at": datetime.now(timezone.utc)
        }
        db.voice_calls.insert_one(doc)
        return True
    except Exception as e:
        logger.error(f"Failed to log voice call to MongoDB: {e}")
        return False


def log_shift_record(date_str: str, weekday: str, login: str, logout: str, status: str, notes: str) -> bool:
    """Upserts a daily shift record in the 'shift_roster' collection."""
    try:
        db = get_db()
        if db is None:
            return False

        db.shift_roster.update_one(
            {"date": date_str},
            {
                "$set": {
                    "date": date_str,
                    "weekday": weekday,
                    "log_in": login,
                    "log_out": logout,
                    "status": status,
                    "notes": notes,
                    "updated_at": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Failed to log shift record to MongoDB: {e}")
        return False


def store_custodian_memory(category: str, key: str, value: Any, tags: Optional[List[str]] = None) -> bool:
    """Stores or updates a long-term knowledge fact in 'memories'."""
    try:
        db = get_db()
        if db is None:
            return False

        db.memories.update_one(
            {"category": category, "key": key},
            {
                "$set": {
                    "category": category,
                    "key": key,
                    "value": value,
                    "tags": tags or [],
                    "updated_at": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Failed to store memory to MongoDB: {e}")
        return False


if __name__ == "__main__":
    import pprint
    print("Testing MongoDB Atlas connection...")
    res = test_connection()
    pprint.pprint(res)
