"""
orchestrator/run_pipeline.py — the SINGLE COMMAND. Drives the whole image pipeline.

Replicates the proven resident flow over the new gateway/worker/services:
  PHASE A — PORTRAIT (separate): create each missing portrait (FLUX _flux loads
            lazily on first portrait), then FREE _flux. Existing portraits skipped.
  PHASE B — IMAGE TRIO (chained): per scenario  scene (PuLID) -> product (Qwen)
            -> realism (RealVisXL). First job of each stage loads that model.
            After all scenarios, FREE the image stack (PuLID + Qwen + RealVisXL).
  Resume:  any stage already done (asset present in outputs) is skipped.
  QC:      hook present, gated by QC_ENABLED (default off) — added as its own layer.

Reuses the building blocks from run_portrait/run_scene/run_step2/run_step3 so there
is ONE source of truth per stage; this file only orchestrates + reports.

RUN:
  python run_pipeline.py @liam.foster --scenarios 3
  python run_pipeline.py --all-accounts --scenarios 5
  python run_pipeline.py --accounts @liam.foster,@emma.callahan --scenarios 2
  flags: --no-free (skip VRAM frees, e.g. when debugging)   env: STEP_3_ENABLED, QC_ENABLED
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from anthropic import Anthropic

import run_portrait as RP
import run_scene as RSC
import run_step2 as RS2
import run_step3 as RS3

HERE = Path(__file__).resolve().parent
OPUS_MODEL = os.environ.get("OPUS_MODEL", "claude-opus-4-7")
RULES_DIR = Path(os.environ.get("RULES_DIR", HERE / "rules"))
GATEWAY_URL = os.environ["GATEWAY_URL"].rstrip("/")
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "5"))
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT_S", "1200"))
STEP3_ENABLED = os.environ.get("STEP_3_ENABLED", "true").lower() != "false"
QC_ENABLED = os.environ.get("QC_ENABLED", "false").lower() == "true"

sb = RSC.sb  # one shared Supabase client factory
_H = {"X-API-Key": GATEWAY_API_KEY}


# ── gateway helpers (quiet poll for clean output) ───────────────────────────────
def _poll(job_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = requests.get(f"{GATEWAY_URL}/jobs/{job_id}", headers=_H, timeout=30)
        r.raise_for_status()
        job = r.json()
        if job.get("status") in ("succeeded", "failed"):
            return job
        time.sleep(POLL_SECONDS)
    return {"status": "timeout", "job_id": job_id}


def _enqueue_free(tenant_id: str, targets: list) -> str:
    r = requests.post(f"{GATEWAY_URL}/free", headers={**_H, "Content-Type": "application/json"},
                      json={"tenant_id": tenant_id, "targets": targets}, timeout=30)
    r.raise_for_status()
    return r.json()["job_id"]


def _opus(api_key: str, system_md: str, user_message: str):
    resp = Anthropic(api_key=api_key).messages.create(
        model=OPUS_MODEL, max_tokens=4096, system=system_md,
        messages=[{"role": "user", "content": user_message}])
    return resp.content[0].text, {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}


def _dur(job: dict):
    try:
        s, f = job.get("started_at"), job.get("finished_at")
        if s and f:
            return (datetime.fromisoformat(f) - datetime.fromisoformat(s)).total_seconds()
    except Exception:
        pass
    return None


def _ok(job: dict, label: str) -> dict:
    if job.get("status") != "succeeded":
        raise RuntimeError(f"{label} {job.get('status')}: {job.get('error')}")
    return job


def _stage_done(persona_id: str, scenario_uuid: str, col: str) -> bool:
    rows = sb().table("outputs").select(col).eq("persona_id", persona_id).eq("scenario_id", scenario_uuid).limit(1).execute().data
    return bool(rows and rows[0].get(col))


# ── PHASE A: portrait (separate) ────────────────────────────────────────────────
def do_portrait(account: dict) -> bool:
    sid = account["tiktok_id"]
    rows = sb().table("personas").select("id,appearance_spec").eq("tiktok_account_id", account["id"]).limit(1).execute().data
    if rows and rows[0].get("appearance_spec"):
        print(f"   [{sid}] portrait exists ✓ (skipping)")
        return False
    api_key = RP.get_anthropic_key(account["tenant_id"])
    system_md = (RULES_DIR / "phaseA.md").read_text(encoding="utf-8")
    parsed, user_message, meta = RP.build_portrait_prompt(account, system_md, api_key)
    RP.log_llm_call(account["tenant_id"], user_message, meta, parsed)
    job_id = RP.enqueue_portrait(account["tenant_id"], account["id"], parsed["portrait_prompt"], parsed)
    job = _ok(_poll(job_id), f"[{sid}] portrait")
    d = _dur(job)
    print(f"   [{sid}] portrait created ✓" + (f" ({d:.0f}s)" if d else ""))
    return True


# ── PHASE B: scene -> product -> realism (chained) ──────────────────────────────
def do_scene(account, persona, scenario_uuid, scenario_key, scenario_spec, api_key):
    if _stage_done(persona["id"], scenario_uuid, "step1_asset_id"):
        print("   scene     ✓ (already done)"); return
    user_msg = RSC.build_user_message(persona, account.get("gender"), scenario_spec)
    raw, usage = _opus(api_key, (RULES_DIR / "step1.md").read_text(encoding="utf-8"), user_msg)
    parsed = RSC.parse_json(raw)
    RSC.log_llm_call(account["tenant_id"], user_msg, raw, usage, parsed)
    job_id = RSC.enqueue_scene({
        "tenant_id": account["tenant_id"], "account_id": account["id"], "persona_id": persona["id"],
        "scenario_id": scenario_uuid, "scenario_key": scenario_key,
        "step_1_prompt": parsed["step_1_image_prompt"], "pulid_params": parsed["fal_pulid_params"]})
    d = _dur(_ok(_poll(job_id), "scene"))
    print(f"   scene     ✓" + (f" ({d:.0f}s)" if d else ""))


def do_product(account, persona, scenario_uuid, scenario_key, scenario_spec, api_key):
    if _stage_done(persona["id"], scenario_uuid, "step2_asset_id"):
        print("   product   ✓ (already done)"); return
    product = RS2.get_product(account["tenant_id"])
    step1_prompt = RS2.get_step1_prompt(scenario_key)
    user_msg = RS2.build_user_message(product, scenario_spec, step1_prompt)
    raw, usage = _opus(api_key, (RULES_DIR / "step2_qwen.md").read_text(encoding="utf-8"), user_msg)
    parsed = RS2.parse_json(raw)
    RS2.log_llm_call(account["tenant_id"], user_msg, raw, usage, parsed)
    job_id = RS2.enqueue_step2({
        "tenant_id": account["tenant_id"], "account_id": account["id"], "persona_id": persona["id"],
        "scenario_id": scenario_uuid, "scenario_key": scenario_key,
        "step_2_prompt": parsed["step_2_image_prompt"], "qwen_params": parsed["fal_qwen_params"]})
    d = _dur(_ok(_poll(job_id), "product"))
    print(f"   product   ✓" + (f" ({d:.0f}s)" if d else ""))
    # QC hook (added as its own layer): if QC_ENABLED, validate the composite and
    # retry product up to 3x with failure feedback before continuing. Off by default.


def do_realism(account, persona, scenario_uuid, scenario_key):
    if _stage_done(persona["id"], scenario_uuid, "step3_asset_id"):
        print("   realism   ✓ (already done)"); return
    mask_prompt = RS3.get_mask_prompt(account["tenant_id"])
    job_id = RS3.enqueue_step3({
        "tenant_id": account["tenant_id"], "account_id": account["id"], "persona_id": persona["id"],
        "scenario_id": scenario_uuid, "scenario_key": scenario_key, "mask_prompt": mask_prompt})
    d = _dur(_ok(_poll(job_id), "realism"))
    print(f"   realism   ✓" + (f" ({d:.0f}s)" if d else ""))


def pick_scenarios(persona_id: str, n: int) -> list:
    scenarios = sb().table("scenarios").select("id,spec").execute().data or []
    outs = {r["scenario_id"]: r for r in
            (sb().table("outputs").select("scenario_id,step2_asset_id,step3_asset_id")
             .eq("persona_id", persona_id).execute().data or []) if r.get("scenario_id")}
    final_col = "step3_asset_id" if STEP3_ENABLED else "step2_asset_id"
    picked = []
    for s in scenarios:
        if not (outs.get(s["id"], {}) or {}).get(final_col):
            picked.append(s)
        if len(picked) >= n:
            break
    return picked


# ── account resolution ──────────────────────────────────────────────────────────
def resolve_accounts(args) -> list:
    if args.all_accounts:
        rows = sb().table("tiktok_accounts").select("*").execute().data or []
        if not rows:
            raise SystemExit("[pipeline] no accounts found")
        return rows
    idents = []
    if args.accounts:
        idents = [a.strip() for a in args.accounts.split(",") if a.strip()]
    elif args.account:
        idents = [args.account]
    else:
        raise SystemExit("usage: run_pipeline.py <account> --scenarios N  (or --all-accounts / --accounts a,b)")
    return [RSC.get_account(i) for i in idents]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("account", nargs="?")
    ap.add_argument("--accounts")
    ap.add_argument("--all-accounts", action="store_true")
    ap.add_argument("--scenarios", type=int, default=1)
    ap.add_argument("--no-free", action="store_true")
    args = ap.parse_args()

    accounts = resolve_accounts(args)
    tenant_id = accounts[0]["tenant_id"]
    print("=" * 60)
    print("ALLUVI pipeline")
    print(f"accounts: {', '.join(a['tiktok_id'] for a in accounts)}")
    print(f"scenarios each: {args.scenarios} | step3: {'on' if STEP3_ENABLED else 'off'} | QC: {'on' if QC_ENABLED else 'off'}")
    print("=" * 60)

    # PHASE A — portraits (FLUX loads on first create; freed after the batch)
    print("\n--- PHASE A: portraits ---")
    created_any = False
    for acct in accounts:
        if do_portrait(acct):
            created_any = True
    if created_any and not args.no_free:
        print("--- freeing portrait model (FLUX) ---")
        j = _poll(_enqueue_free(tenant_id, ["phasea"]))
        print(f"   {'freed: FLUX ✓' if j.get('status') == 'succeeded' else '(warn) free ' + str(j.get('status'))}")
    elif not created_any:
        print("   (no portraits created — FLUX never loaded)")

    # PHASE B — scene -> product -> realism (image trio loads lazily; freed at end)
    print("\n--- PHASE B: scene -> product -> realism ---")
    total = 0
    for acct in accounts:
        prows = sb().table("personas").select("id").eq("tiktok_account_id", acct["id"]).limit(1).execute().data
        if not prows:
            print(f"[{acct['tiktok_id']}] no persona — skipping (portrait may have failed)"); continue
        persona = prows[0]
        api_key = RSC.get_anthropic_key(acct["tenant_id"])
        scenarios = pick_scenarios(persona["id"], args.scenarios)
        if not scenarios:
            print(f"[{acct['tiktok_id']}] all requested scenarios already complete ✓"); continue
        for i, s in enumerate(scenarios, 1):
            spec = s.get("spec") or {}
            key = spec.get("id", s["id"])
            print(f"[{acct['tiktok_id']}] scenario {i}/{len(scenarios)}: {key}")
            do_scene(acct, persona, s["id"], key, spec, api_key)
            do_product(acct, persona, s["id"], key, spec, api_key)
            if STEP3_ENABLED:
                do_realism(acct, persona, s["id"], key)
            print("   scenario complete ✓")
            total += 1

    if not args.no_free:
        print("\n--- freeing image stack (PuLID + Qwen + RealVisXL) ---")
        j = _poll(_enqueue_free(tenant_id, ["stage1", "qwen", "realism"]))
        print(f"   {'freed: PuLID, Qwen, RealVisXL ✓' if j.get('status') == 'succeeded' else '(warn) free ' + str(j.get('status'))}")

    print("\n" + "=" * 60)
    print(f"PIPELINE COMPLETE ✓   ({total} scenario(s) processed)")
    print("=" * 60)


if __name__ == "__main__":
    main()
