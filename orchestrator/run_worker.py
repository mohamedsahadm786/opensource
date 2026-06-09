"""
orchestrator/run_worker.py — pipeline job consumer (makes the web "Run" button live).

The web's trigger-pipeline Edge Function enqueues a durable job: jobs(job_type='pipeline_run',
status='queued', tenant_id). This daemon claims those jobs and runs the proven CLI for that tenant —
`python run_pipeline.py --tenant <slug>` — which already reads tenant_pipeline_config (creation_mode,
num_videos_per_account, video_mode, duration, toggles), so the web's Run settings drive everything.

Design:
  * ONE worker per GPU. It runs each pipeline synchronously (one at a time = one GPU), so it never
    claims a second job while a run is in flight.
  * claim_next_job() atomically leases a queued job (and reclaims jobs left 'running' past their
    visibility timeout, up to max_retries — so a worker CRASH recovers automatically).
  * Explicit pipeline failure -> mark the job 'failed' with the error (fail-fast; no blind GPU retry).
  * After claiming, the job's visibility timeout is widened so a long (multi-video) run is never
    mistaken for "stuck" and double-claimed if you ever run more than one worker.

RUN (one terminal on the machine that has the orchestrator + .env):
  cd orchestrator && python run_worker.py
"""
from __future__ import annotations

import os
import sys
import time
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

WORKER_ID = f"pipeline-worker-{socket.gethostname()}-{os.getpid()}"
POLL_SECONDS = int(os.environ.get("PIPELINE_WORKER_POLL_S", "5"))
RUN_TIMEOUT_S = int(os.environ.get("PIPELINE_RUN_TIMEOUT_S", "21600"))   # 6h hard cap per run
LEASE_S = int(os.environ.get("PIPELINE_LEASE_S", "21600"))              # widen the job lease to match

_sb: Client | None = None


def sb() -> Client:
    global _sb
    if _sb is None:
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    return _sb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def claim_job() -> dict | None:
    rows = sb().rpc("claim_next_job",
                    {"p_worker_id": WORKER_ID, "p_job_types": ["pipeline_run"]}).execute().data
    return (rows[0] if rows else None)


def tenant_slug(tenant_id: str) -> str | None:
    r = sb().table("tenants").select("slug").eq("id", tenant_id).limit(1).execute().data
    return r[0]["slug"] if r else None


def finish(job_id: str, status: str, error: str | None = None, result: dict | None = None) -> None:
    upd = {"status": status, "finished_at": _now()}
    if error:
        upd["error"] = str(error)[:4000]
    if result is not None:
        upd["result"] = result
    sb().table("jobs").update(upd).eq("id", job_id).execute()
    print(f"[pipeline-worker] job {job_id} -> {status}", flush=True)


def run_job(job: dict) -> None:
    job_id = job["id"]
    slug = tenant_slug(job["tenant_id"])
    if not slug:
        finish(job_id, "failed", error=f"tenant {job['tenant_id']} has no slug"); return

    # widen the lease so a long run isn't reclaimed as 'stuck' by another worker
    try:
        sb().table("jobs").update({"visibility_timeout_seconds": LEASE_S}).eq("id", job_id).execute()
    except Exception:
        pass

    print(f"[pipeline-worker] job {job_id}: run_pipeline.py --tenant {slug}", flush=True)
    try:
        # Force UTF-8 for the child: its captured (piped) stdout would otherwise
        # default to the Windows locale code page (cp1252), which can't encode the
        # pipeline's ✓/—/→ glyphs and crashes print(). PYTHONUTF8 fixes the child's
        # stdout; encoding+errors make the parent decode the pipe safely too.
        child_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, str(HERE / "run_pipeline.py"), "--tenant", slug],
            cwd=str(HERE), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=child_env, timeout=RUN_TIMEOUT_S)
        if proc.returncode == 0:
            finish(job_id, "succeeded", result={"slug": slug, "stdout_tail": (proc.stdout or "")[-2000:]})
        else:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            finish(job_id, "failed", error=f"run_pipeline exit {proc.returncode}: {tail}")
    except subprocess.TimeoutExpired:
        finish(job_id, "failed", error=f"run_pipeline timed out after {RUN_TIMEOUT_S}s")
    except Exception as e:
        finish(job_id, "failed", error=f"{type(e).__name__}: {e}")


def main() -> None:
    print(f"[pipeline-worker] {WORKER_ID} polling 'pipeline_run' every {POLL_SECONDS}s", flush=True)
    while True:
        try:
            job = claim_job()
            if job:
                run_job(job)
            else:
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print("[pipeline-worker] stopped", flush=True); break
        except Exception as e:
            print(f"[pipeline-worker] loop error: {type(e).__name__}: {e}", flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
