"""
orchestrator/run_step3.py — LOCAL trigger for Step 3 (realism). NO Claude.

Stage 3 is static: the realism prompt + SDXL params live in step_3_realism.py.
The only per-tenant input is products.mask_prompt (protects the product box).
This orchestrator just resolves the scene (must have step2 done) + the mask_prompt
and enqueues a step3 job; the worker runs the realism pass on :8194.

RUN:
  python run_step3.py @liam.foster gym_post_workout_mirror_01     # specific (must have step2 done)
  python run_step3.py @liam.foster next                           # first scene with step2 done, step3 not
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client, Client

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

GATEWAY_URL = os.environ["GATEWAY_URL"].rstrip("/")
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "5"))
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT_S", "900"))

_sb: Client | None = None
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def sb() -> Client:
    global _sb
    if _sb is None:
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    return _sb


def get_account(ident: str) -> dict:
    t = sb().table("tiktok_accounts").select("*")
    rows = (t.eq("id", ident) if _UUID.match(ident) else t.eq("tiktok_id", ident)).limit(1).execute().data
    if not rows and not _UUID.match(ident) and not ident.startswith("@"):
        rows = sb().table("tiktok_accounts").select("*").eq("tiktok_id", "@" + ident).limit(1).execute().data
    if not rows:
        sys.exit(f"[step3] no account matching {ident!r}")
    return rows[0]


def get_persona(account_id: str) -> dict:
    rows = sb().table("personas").select("id").eq("tiktok_account_id", account_id).limit(1).execute().data
    if not rows:
        sys.exit("[step3] no persona for this account")
    return rows[0]


def pick_output(persona_id: str, selector: str) -> dict:
    """Return an outputs row that HAS step2 done (so a composite exists to refine)."""
    rows = sb().table("outputs").select("scenario_id,scenario_key,step2_asset_id,step3_asset_id") \
        .eq("persona_id", persona_id).execute().data or []
    have_step2 = [r for r in rows if r.get("step2_asset_id")]
    if selector and selector != "next":
        for r in have_step2:
            if r.get("scenario_key") == selector:
                return r
        sys.exit(f"[step3] no step2-done output for scenario {selector!r}. Run step2 first.")
    for r in have_step2:
        if not r.get("step3_asset_id"):
            return r
    sys.exit("[step3] no composite with step2 done and step3 pending for this persona")


def get_mask_prompt(tenant_id: str) -> str:
    rows = sb().table("products").select("mask_prompt").eq("tenant_id", tenant_id).limit(1).execute().data
    mp = (rows[0].get("mask_prompt") if rows else None)
    if not mp:
        sys.exit("[step3] product has no mask_prompt")
    return mp


def enqueue_step3(payload: dict) -> str:
    res = requests.post(f"{GATEWAY_URL}/step3",
                        headers={"X-API-Key": GATEWAY_API_KEY, "Content-Type": "application/json"},
                        json=payload, timeout=30)
    res.raise_for_status()
    return res.json()["job_id"]


def poll(job_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        res = requests.get(f"{GATEWAY_URL}/jobs/{job_id}", headers={"X-API-Key": GATEWAY_API_KEY}, timeout=30)
        res.raise_for_status()
        job = res.json()
        print(f"[step3]   job {job_id} -> {job.get('status')}")
        if job.get("status") in ("succeeded", "failed"):
            return job
        time.sleep(POLL_SECONDS)
    return {"status": "timeout", "job_id": job_id}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        sys.exit('usage: python run_step3.py <account> <scenario_key|next>')
    account = get_account(args[0])
    tenant_id = account["tenant_id"]
    persona = get_persona(account["id"])
    out_row = pick_output(persona["id"], args[1])
    scenario_id = out_row["scenario_id"]
    scenario_key = out_row.get("scenario_key") or scenario_id
    mask_prompt = get_mask_prompt(tenant_id)
    print(f"[step3] account={account['tiktok_id']} scenario={scenario_key} mask_prompt={mask_prompt!r}")

    job_id = enqueue_step3({
        "tenant_id": tenant_id, "account_id": account["id"], "persona_id": persona["id"],
        "scenario_id": scenario_id, "scenario_key": scenario_key, "mask_prompt": mask_prompt,
    })
    print(f"[step3] enqueued job {job_id}; polling…")
    final = poll(job_id)
    print(f"\n[step3] FINAL: {json.dumps(final, indent=2)}")


if __name__ == "__main__":
    main()
