#!/usr/bin/env python3
"""
⚡ Aethelgard Gateway Telegram Typing Lifecycle Hotfix & Proxy Hardener
Addresses Hermes Gateway typing indicator loops, unclosed typing actions,
and auto-TTS background typing spam across all execution modes.
"""

import os
import sys
import logging
import asyncio
from contextlib import suppress
from typing import Optional, Dict, Any

logger = logging.getLogger("aethelgard.typing_patch")


def ensure_hermes_on_path() -> None:
    """Ensure Hermes Agent runtime directory is in sys.path."""
    try:
        import gateway.run_turn  # type: ignore # already importable
        return
    except ImportError:
        pass

    candidates = [
        os.environ.get("HERMES_AGENT_DIR"),
        r"C:\Users\omara\AppData\Local\hermes\hermes-agent",
        os.path.expanduser("~/AppData/Local/hermes/hermes-agent"),
        os.path.expanduser("~/.local/share/hermes/hermes-agent"),
        os.path.expanduser("~/hermes-agent"),
        os.path.expanduser("~/venv/lib/python3.11/site-packages"),
        os.path.expanduser("~/.venv/lib/python3.11/site-packages"),
        "/opt/hermes/hermes-agent",
    ]
    for c in candidates:
        if c and os.path.exists(c) and c not in sys.path:
            if os.path.exists(os.path.join(c, "gateway", "run_turn.py")) or os.path.exists(os.path.join(c, "gateway")):
                sys.path.insert(0, c)
                break


def apply_typing_lifecycle_patches() -> Dict[str, bool]:
    """
    Applies comprehensive runtime monkeypatches to Hermes Gateway and verifies
    proxy hardening settings.

    Returns:
        Dict[str, bool]: Verification status of each patch component.
    """
    ensure_hermes_on_path()

    results: Dict[str, bool] = {
        "hmwa_stop_typing_for_turn": False,
        "keep_typing": False,
        "telegram_adapter_send": False,
        "auto_tts_typing_silence": False,
        "proxy_threading_server": False,
        "proxy_bounded_timeouts": False,
    }

    # 1. Patch RunTurnRunner / GatewayTurnMixin._hmwa_stop_typing_for_turn
    try:
        import gateway.run_turn  # type: ignore
        TurnClass = getattr(gateway.run_turn, "RunTurnRunner", None) or getattr(gateway.run_turn, "GatewayTurnMixin", None)

        if TurnClass is not None:
            if not hasattr(gateway.run_turn, "RunTurnRunner"):
                setattr(gateway.run_turn, "RunTurnRunner", TurnClass)

            if not getattr(TurnClass, "_run_turn_typing_patched", False):
                orig_stop = getattr(TurnClass, "_hmwa_stop_typing_for_turn", None)

                async def patched_stop_typing_for_turn(self, event, source):
                    if orig_stop:
                        with suppress(Exception):
                            await orig_stop(self, event, source)
                    with suppress(Exception):
                        adapter = self._adapter_for_source(source)
                        if adapter is not None:
                            chat_id = getattr(source, "chat_id", None)
                            if chat_id is not None:
                                cid_str = str(chat_id)
                                if hasattr(adapter, "pause_typing_for_chat") and callable(adapter.pause_typing_for_chat):
                                    adapter.pause_typing_for_chat(chat_id)
                                if not hasattr(adapter, "_typing_paused") or adapter._typing_paused is None:
                                    adapter._typing_paused = set()
                                adapter._typing_paused.add(cid_str)
                                adapter._typing_paused.add(chat_id)
                                if hasattr(adapter, "stop_typing") and callable(adapter.stop_typing):
                                    res = adapter.stop_typing(chat_id)
                                    if asyncio.iscoroutine(res):
                                        await res

                TurnClass._hmwa_stop_typing_for_turn = patched_stop_typing_for_turn
                TurnClass._run_turn_typing_patched = True

            results["hmwa_stop_typing_for_turn"] = True
    except Exception as e:
        logger.warning("[gateway_typing_patch] Failed to patch RunTurnRunner: %s", e)

    # 2. Patch BasePlatformAdapter._keep_typing
    try:
        from gateway.platforms.base import BasePlatformAdapter  # type: ignore

        if not getattr(BasePlatformAdapter, "_base_adapter_typing_patched", False):
            orig_keep = getattr(BasePlatformAdapter, "_keep_typing", None)

            async def patched_keep_typing(self, chat_id: str, interval: float = 2.0, metadata=None,
                                          stop_event: Optional[asyncio.Event] = None) -> None:
                cid_str = str(chat_id)
                # Exit immediately if chat_id in self._typing_paused or stop_event is set
                if hasattr(self, "_typing_paused") and (cid_str in self._typing_paused or chat_id in self._typing_paused):
                    return
                if stop_event is not None and stop_event.is_set():
                    return

                _send_typing_timeout = max(0.25, min(1.5, interval - 0.25))
                try:
                    while True:
                        if stop_event is not None and stop_event.is_set():
                            return
                        if hasattr(self, "_typing_paused") and (cid_str in self._typing_paused or chat_id in self._typing_paused):
                            return
                        try:
                            await asyncio.wait_for(self.send_typing(chat_id, metadata=metadata),
                                                   timeout=_send_typing_timeout)
                        except asyncio.TimeoutError:
                            pass
                        except Exception as typing_err:
                            pass

                        if stop_event is None:
                            await asyncio.sleep(interval)
                            continue

                        loop = asyncio.get_running_loop()
                        deadline = loop.time() + interval
                        while not stop_event.is_set():
                            if hasattr(self, "_typing_paused") and (cid_str in self._typing_paused or chat_id in self._typing_paused):
                                return
                            remaining = deadline - loop.time()
                            if remaining <= 0:
                                break
                            await asyncio.sleep(min(0.25, remaining))
                except asyncio.CancelledError:
                    pass
                finally:
                    with suppress(Exception):
                        if hasattr(self, "_stop_typing_quietly"):
                            await self._stop_typing_quietly(chat_id, metadata)
                    if hasattr(self, "_typing_paused"):
                        self._typing_paused.discard(chat_id)
                        self._typing_paused.discard(cid_str)
                    if hasattr(self, "_status_text") and isinstance(self._status_text, dict):
                        self._status_text.pop(cid_str, None)

            BasePlatformAdapter._keep_typing = patched_keep_typing

            def base_pause_typing_for_chat(self, chat_id: str) -> None:
                cid_str = str(chat_id)
                if not hasattr(self, "_typing_paused") or self._typing_paused is None:
                    self._typing_paused = set()
                self._typing_paused.add(cid_str)
                self._typing_paused.add(chat_id)

            BasePlatformAdapter.pause_typing_for_chat = base_pause_typing_for_chat
            BasePlatformAdapter._base_adapter_typing_patched = True

        results["keep_typing"] = True
    except Exception as e:
        logger.warning("[gateway_typing_patch] Failed to patch BasePlatformAdapter: %s", e)

    # 3. Patch TelegramAdapter._retrigger_typing and stop_typing
    try:
        from plugins.platforms.telegram.adapter import TelegramAdapter  # type: ignore

        if not getattr(TelegramAdapter, "_telegram_adapter_typing_patched", False):
            orig_retrigger = getattr(TelegramAdapter, "_retrigger_typing", None)

            async def patched_retrigger_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
                """
                Strictly skip re-triggering typing on final messages, single-chunk deliveries,
                and when metadata lacks intermediate stream flags.
                """
                if not metadata or not metadata.get("_hermes_is_intermediate_chunk"):
                    return
                if metadata.get("notify") or metadata.get("is_final") or metadata.get("final"):
                    return
                cid_str = str(chat_id)
                if hasattr(self, "_typing_paused") and (cid_str in self._typing_paused or chat_id in self._typing_paused):
                    return
                if orig_retrigger:
                    await orig_retrigger(self, chat_id, metadata)

            TelegramAdapter._retrigger_typing = patched_retrigger_typing

            async def telegram_stop_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
                """Concrete stop_typing: marks chat paused in _typing_paused and clears cooldown."""
                cid_str = str(chat_id)
                if not hasattr(self, "_typing_paused") or self._typing_paused is None:
                    self._typing_paused = set()
                self._typing_paused.add(cid_str)
                self._typing_paused.add(chat_id)
                cooldown_map = getattr(self, "_telegram_typing_cooldown_until", None)
                if isinstance(cooldown_map, dict):
                    cooldown_map.pop(cid_str, None)
                    cooldown_map.pop(chat_id, None)

            TelegramAdapter.stop_typing = telegram_stop_typing

            def telegram_pause_typing_for_chat(self, chat_id: str) -> None:
                cid_str = str(chat_id)
                if not hasattr(self, "_typing_paused") or self._typing_paused is None:
                    self._typing_paused = set()
                self._typing_paused.add(cid_str)
                self._typing_paused.add(chat_id)

            TelegramAdapter.pause_typing_for_chat = telegram_pause_typing_for_chat
            TelegramAdapter._telegram_adapter_typing_patched = True

        results["telegram_adapter_send"] = True
    except Exception as e:
        logger.warning("[gateway_typing_patch] Failed to patch TelegramAdapter: %s", e)

    # 4. Patch RunVoiceRunner / GatewayVoiceMixin._send_voice_reply
    try:
        import gateway.run_voice  # type: ignore
        VoiceClass = getattr(gateway.run_voice, "RunVoiceRunner", None) or getattr(gateway.run_voice, "GatewayVoiceMixin", None)

        if VoiceClass is not None:
            if not hasattr(gateway.run_voice, "RunVoiceRunner"):
                setattr(gateway.run_voice, "RunVoiceRunner", VoiceClass)

            if not getattr(VoiceClass, "_run_voice_typing_patched", False):
                orig_send_voice = getattr(VoiceClass, "_send_voice_reply", None)

                async def patched_send_voice_reply(self, event, text: str) -> None:
                    chat_id = getattr(getattr(event, "source", None), "chat_id", None)
                    adapter = None
                    with suppress(Exception):
                        adapter = self._adapter_for_source(event.source)
                    if adapter is not None and chat_id is not None:
                        cid_str = str(chat_id)
                        if hasattr(adapter, "pause_typing_for_chat") and callable(adapter.pause_typing_for_chat):
                            adapter.pause_typing_for_chat(chat_id)
                        if not hasattr(adapter, "_typing_paused") or adapter._typing_paused is None:
                            adapter._typing_paused = set()
                        adapter._typing_paused.add(cid_str)
                        adapter._typing_paused.add(chat_id)

                    try:
                        if orig_send_voice:
                            await orig_send_voice(self, event, text)
                    finally:
                        # Keep typing paused after voice note delivery
                        if adapter is not None and chat_id is not None:
                            cid_str = str(chat_id)
                            if hasattr(adapter, "pause_typing_for_chat") and callable(adapter.pause_typing_for_chat):
                                adapter.pause_typing_for_chat(chat_id)
                            if not hasattr(adapter, "_typing_paused") or adapter._typing_paused is None:
                                adapter._typing_paused = set()
                            adapter._typing_paused.add(cid_str)
                            adapter._typing_paused.add(chat_id)

                VoiceClass._send_voice_reply = patched_send_voice_reply
                VoiceClass._run_voice_typing_patched = True

            results["auto_tts_typing_silence"] = True
    except Exception as e:
        logger.warning("[gateway_typing_patch] Failed to patch RunVoiceRunner: %s", e)

    # 5. Verify Antigravity Proxy settings
    try:
        import antigravity_proxy
        th_server = getattr(antigravity_proxy, "ThreadingHTTPServer", None)
        results["proxy_threading_server"] = th_server is not None and issubclass(th_server, object)
        up_timeout = getattr(antigravity_proxy, "UPSTREAM_TIMEOUT", 90)
        budget = getattr(antigravity_proxy, "MAX_FAILOVER_BUDGET_SECONDS", 999)
        results["proxy_bounded_timeouts"] = bool(up_timeout <= 20 and budget <= 35)
    except Exception as e:
        logger.warning("[gateway_typing_patch] Failed to check antigravity_proxy: %s", e)

    return results


def patch_hermes_gateway_typing() -> Dict[str, bool]:
    """Alias for apply_typing_lifecycle_patches."""
    return apply_typing_lifecycle_patches()


if __name__ == "__main__":
    res = apply_typing_lifecycle_patches()
    print("Hermes Gateway typing lifecycle patch results:", res)
    assert all(res.values()), f"Some patches failed: {res}"
    print("✅ All typing lifecycle patches applied and verified successfully.")
