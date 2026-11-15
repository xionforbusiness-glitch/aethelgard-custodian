#!/usr/bin/env python3
"""
Aethelgard Async "Call Me Back When Done" Orchestrator
=====================================================
Manages background long-running tasks and event watches so Omar never waits
on spin loops or hung chat responses:
  - `hermes_task`: Executes heavy deep-research / codebase / CLI jobs asynchronously
    (strictly capped at `max_concurrent_workers=1` to protect 1GB e2-micro RAM).
  - `watch_reply`: Polls Gmail / LinkedIn for an expected reply or keyword and notifies on arrival.
  - `timed_callback`: Fires a proactive voice call or summary at an exact UTC timestamp.

Respects `circadian_oracle.get_operational_mode` (suppresses calls during `SLEEP_SHIELD`,
delivering silent Telegram voice/text summary instead unless marked `urgent=True`).
"""

import datetime
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


_ACTIVE_LOCK = threading.Lock()
_ACTIVE_WORKERS = 0
MAX_CONCURRENT_WORKERS = 1


def _get_state_dir() -> Path:
    override = os.environ.get("AETHELGARD_STATE_DIR")
    if override:
        p = Path(os.path.expanduser(override))
    else:
        home = os.environ.get("AETHELGARD_HOME")
        base = Path(os.path.expanduser(home)) if home else (Path.home() / ".aethelgard")
        p = base / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_jobs_path() -> Path:
    return _get_state_dir() / "async_jobs.json"


def _load_jobs() -> List[Dict[str, Any]]:
    path = get_jobs_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            return data["jobs"]
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_jobs(jobs: List[Dict[str, Any]]) -> None:
    path = get_jobs_path()
    path.write_text(json.dumps({"jobs": jobs}, indent=2, ensure_ascii=False), encoding="utf-8")


def enqueue_async_job(
    kind: str,
    title: str,
    payload: Optional[Dict[str, Any]] = None,
    delivery: str = "call",
    urgent: bool = False,
) -> Dict[str, Any]:
    """
    Queues an asynchronous background job (`hermes_task`, `watch_reply`, or `timed_callback`).
    """
    valid_kinds = ("hermes_task", "watch_reply", "timed_callback")
    norm_kind = kind if kind in valid_kinds else "hermes_task"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    job: Dict[str, Any] = {
        "job_id": f"job_{uuid.uuid4().hex[:8]}",
        "kind": norm_kind,
        "title": title.strip() or norm_kind,
        "payload": payload or {},
        "delivery": delivery if delivery in ("call", "telegram", "both") else "call",
        "urgent": bool(urgent),
        "status": "pending",
        "created_at": now_iso,
        "updated_at": now_iso,
        "result_summary": "",
        "error": "",
    }
    jobs = _load_jobs()
    jobs.insert(0, job)
    _save_jobs(jobs)
    return job


def list_async_jobs(status: Optional[str] = None) -> List[Dict[str, Any]]:
    jobs = _load_jobs()
    if not status or status == "all":
        return jobs
    return [j for j in jobs if j.get("status") == status]


def _deliver_job_notification(
    job: Dict[str, Any],
    summary: str,
    vault_dir: Optional[str] = None,
    call_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
    telegram_fn: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    """Delivers callback notice respecting Circadian Sleep Shield."""
    allow_call = True
    try:
        import circadian_oracle
        op = circadian_oracle.get_operational_mode(vault_dir=vault_dir)
        allow_call = op.get("allow_proactive_calls", True) or bool(job.get("urgent"))
    except Exception:
        allow_call = True

    delivery = job.get("delivery", "call")
    spoke_text = f"Callback ready for {job.get('title')}. {summary}"
    outcome: Dict[str, Any] = {"called": False, "telegram_sent": False, "sleep_suppressed": False}

    if delivery in ("call", "both"):
        if allow_call:
            try:
                if call_fn:
                    res = call_fn(spoke_text)
                else:
                    import telegram_voice_caller
                    res = telegram_voice_caller.request_call_via_rpc_or_direct(opening_prompt=spoke_text)
                outcome["called"] = bool(res and res.get("success"))
                outcome["call_result"] = res
            except Exception as exc:
                outcome["call_error"] = str(exc)
        else:
            outcome["sleep_suppressed"] = True

    # Always fall back to Telegram notification if call wasn't placed or delivery=='telegram'/'both'
    if delivery in ("telegram", "both") or not outcome["called"]:
        try:
            tg_msg = f"🔔 *Async Task Complete: {job.get('title')}*\n\n{summary}"
            if telegram_fn:
                outcome["telegram_sent"] = bool(telegram_fn(tg_msg))
            else:
                from proactive_scheduler import send_telegram
                outcome["telegram_sent"] = bool(send_telegram(tg_msg))
        except Exception as exc:
            outcome["telegram_error"] = str(exc)

    return outcome


def _run_hermes_task_worker(job_id: str, command_or_prompt: str, timeout_s: int = 180) -> None:
    global _ACTIVE_WORKERS
    result_summary = ""
    error_text = ""
    try:
        proc = subprocess.run(
            command_or_prompt,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0:
            result_summary = (out or "Task finished cleanly.")[:800]
        else:
            error_text = (err or out or f"Exit code {proc.returncode}")[:500]
    except Exception as exc:
        error_text = str(exc)[:400]
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_WORKERS = max(0, _ACTIVE_WORKERS - 1)

    jobs = _load_jobs()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    target_job = None
    for j in jobs:
        if j.get("job_id") == job_id:
            j["status"] = "failed" if error_text else "completed"
            j["updated_at"] = now_iso
            j["result_summary"] = result_summary or error_text
            j["error"] = error_text
            target_job = j
            break
    _save_jobs(jobs)
    if target_job:
        _deliver_job_notification(target_job, target_job["result_summary"])


def _evaluate_watch_reply(job: Dict[str, Any]) -> Optional[str]:
    """Checks Gmail (and optional LinkedIn) for a matching reply since job creation."""
    payload = job.get("payload") or {}
    query = str(payload.get("query") or payload.get("from") or "").strip()
    if not query:
        return None
    try:
        from omnichannel_bridge import GoogleAdapter
        ga = GoogleAdapter()
        if ga.get_status().get("status") != "connected":
            return None
        hits = ga.search_emails(query=query, max_results=3)
        if not hits:
            return None
        seen_ids = set(payload.get("initial_msg_ids") or [])
        for h in hits:
            mid = str(h.get("id") or "")
            if mid and mid not in seen_ids:
                subj = h.get("subject", "New message")
                sender = h.get("sender", query)
                return f"Reply arrived from {sender}: {subj}"
    except Exception:
        pass
    return None


def tick_async_jobs(
    now: Optional[datetime.datetime] = None,
    vault_dir: Optional[str] = None,
    call_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
    telegram_fn: Optional[Callable[[str], bool]] = None,
    spawn_workers: bool = True,
) -> Dict[str, Any]:
    """
    Evaluates pending async jobs once. Intended to be called every scheduler cycle (~30s).
    Returns summary counters for observability and testing.
    """
    global _ACTIVE_WORKERS
    ref_now = now or datetime.datetime.now(datetime.timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=datetime.timezone.utc)

    jobs = _load_jobs()
    triggered = 0
    started_workers = 0
    changed = False

    for job in jobs:
        if job.get("status") != "pending":
            continue
        kind = job.get("kind")
        payload = job.get("payload") or {}

        if kind == "timed_callback":
            trigger_str = str(payload.get("trigger_at") or "").strip()
            due = False
            if trigger_str:
                try:
                    t_dt = datetime.datetime.fromisoformat(trigger_str)
                    if t_dt.tzinfo is None:
                        t_dt = t_dt.replace(tzinfo=datetime.timezone.utc)
                    due = ref_now >= t_dt
                except Exception:
                    due = False
            if due:
                summary = str(payload.get("message") or job.get("title") or "Scheduled callback time reached.")
                delivery_res = _deliver_job_notification(
                    job, summary, vault_dir=vault_dir, call_fn=call_fn, telegram_fn=telegram_fn
                )
                job["status"] = "completed"
                job["updated_at"] = ref_now.isoformat()
                job["result_summary"] = summary
                job["delivery_receipt"] = delivery_res
                triggered += 1
                changed = True

        elif kind == "watch_reply":
            match_msg = _evaluate_watch_reply(job)
            if match_msg:
                delivery_res = _deliver_job_notification(
                    job, match_msg, vault_dir=vault_dir, call_fn=call_fn, telegram_fn=telegram_fn
                )
                job["status"] = "completed"
                job["updated_at"] = ref_now.isoformat()
                job["result_summary"] = match_msg
                job["delivery_receipt"] = delivery_res
                triggered += 1
                changed = True

        elif kind == "hermes_task" and spawn_workers:
            cmd = str(payload.get("command") or "").strip()
            if cmd:
                with _ACTIVE_LOCK:
                    if _ACTIVE_WORKERS < MAX_CONCURRENT_WORKERS:
                        _ACTIVE_WORKERS += 1
                        job["status"] = "running"
                        job["updated_at"] = ref_now.isoformat()
                        changed = True
                        started_workers += 1
                        threading.Thread(
                            target=_run_hermes_task_worker,
                            args=(job["job_id"], cmd, int(payload.get("timeout_s", 180))),
                            daemon=True,
                        ).start()

    if changed:
        _save_jobs(jobs)

    return {
        "checked": len(jobs),
        "triggered": triggered,
        "started_workers": started_workers,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aethelgard Async Callback Orchestrator")
    parser.add_argument("--list", action="store_true", help="List queued/recent async jobs")
    parser.add_argument("--tick", action="store_true", help="Evaluate pending jobs once")
    parser.add_argument("--enqueue-callback", metavar="TITLE", help="Queue a timed callback")
    parser.add_argument("--in-minutes", type=int, default=15, help="Delay in minutes for timed callback")
    parser.add_argument("--watch-email", metavar="QUERY", help="Queue an email reply watcher")
    parser.add_argument("--delivery", default="call", choices=["call", "telegram", "both"])
    args = parser.parse_args()

    if args.enqueue_callback:
        t_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=args.in_minutes)).isoformat()
        print(json.dumps(enqueue_async_job(
            "timed_callback",
            args.enqueue_callback,
            payload={"trigger_at": t_at, "message": args.enqueue_callback},
            delivery=args.delivery,
        ), indent=2))
        return 0
    if args.watch_email:
        print(json.dumps(enqueue_async_job(
            "watch_reply",
            f"Watch: {args.watch_email}",
            payload={"query": args.watch_email},
            delivery=args.delivery,
        ), indent=2))
        return 0
    if args.tick:
        print(json.dumps(tick_async_jobs(), indent=2))
        return 0
    if args.list:
        print(json.dumps(list_async_jobs("all"), indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
