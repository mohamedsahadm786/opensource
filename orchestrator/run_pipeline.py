"""
orchestrator/run_pipeline.py — the SINGLE COMMAND. Drives the whole image+video pipeline.

Replicates the proven resident flow over the new gateway/worker/services:
  PHASE A — PORTRAIT (separate): create each missing portrait (FLUX _flux loads
            lazily on first portrait), then FREE _flux. Existing portraits skipped.
  PHASE B — IMAGE TRIO (chained): per scenario  scene (PuLID) -> product (Qwen)
            -> realism (RealVisXL). After all scenarios, FREE the image stack.
  PHASE C — VIDEO (per finished image): script (Opus, rules/script.md + the tenant's
            3 DB sources) -> enqueue /video -> the worker assembles on the video-service
            -> mp4 in the videos bucket. After all videos, FREE the video stack.
  Resume:  any stage already done (asset/video present) is skipped.

Controls come from tenant_pipeline_config (the web execution page writes it):
  creation_mode (all|new_only|specific) -> which accounts     [only with --tenant]
  num_videos_per_account                -> scenarios per account (= images = videos)
  video_mode / duration / shot_seconds / silentfirst knobs -> the video build
CLI flags override the config for testing.

RUN:
  python run_pipeline.py @liam.foster --scenarios 3                 # one account, 3 images+videos
  python run_pipeline.py --tenant alluvi                            # DB-driven: creation_mode + counts
  python run_pipeline.py @liam.foster --scenarios 1 --skip-videos   # images only
  python run_pipeline.py @liam.foster --video-mode silentfirst      # override the assembly mode
  flags: --no-free (keep models resident)   env: STEP_3_ENABLED, QC_ENABLED
"""

from __future__ import annotations

import argparse
import json
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
import run_video as RV
import script_gen as SG
import qc as QC
import engine as ENG

HERE = Path(__file__).resolve().parent
OPUS_MODEL = os.environ.get("OPUS_MODEL", "claude-opus-4-7")
RULES_DIR = Path(os.environ.get("RULES_DIR", HERE / "rules"))
GATEWAY_URL = os.environ["GATEWAY_URL"].rstrip("/")
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "5"))
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT_S", "1200"))
VIDEO_POLL_TIMEOUT = int(os.environ.get("VIDEO_POLL_TIMEOUT_S", "10800"))  # 3h — video is slow
STEP3_ENABLED = os.environ.get("STEP_3_ENABLED", "true").lower() != "false"
QC_ENABLED = os.environ.get("QC_ENABLED", "true").lower() != "false"

sb = RSC.sb  # one shared Supabase client factory
_H = {"X-API-Key": GATEWAY_API_KEY}


# ── gateway helpers (quiet poll for clean output) ───────────────────────────────
def _poll(job_id: str, timeout: int = POLL_TIMEOUT) -> dict:
    deadline = time.time() + timeout
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


# ── live progress marker (the web polls stage_executions to show "what stage now") ─
_TENANT_ID = None  # set in main(); read by progress()

def progress(stage: str) -> None:
    """Best-effort: write the current stage so the web's Run pill shows live
    progress (e.g. 'Compositing the product (Qwen)…'). Never raises — progress
    reporting must never break the pipeline."""
    if not _TENANT_ID:
        return
    try:
        sb().table("stage_executions").insert(
            {"tenant_id": _TENANT_ID, "stage_name": stage, "status": "running"}).execute()
    except Exception:
        pass


# ── PHASE A: portrait (separate) ────────────────────────────────────────────────
def do_portrait(account: dict) -> bool:
    sid = account["tiktok_id"]
    rows = sb().table("personas").select("id,appearance_spec").eq("tiktok_account_id", account["id"]).limit(1).execute().data
    if rows and rows[0].get("appearance_spec"):
        print(f"   [{sid}] portrait exists ✓ (skipping)")
        return False
    progress("phasea")
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
    progress("step1")
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


def get_product_full(tenant_id: str) -> dict:
    rows = sb().table("products").select("*").eq("tenant_id", tenant_id).limit(1).execute().data
    if not rows:
        raise RuntimeError("no product for tenant")
    return rows[0]


def _download_step2(persona_id: str, scenario_uuid: str):
    o = sb().table("outputs").select("id,step2_asset_id").eq("persona_id", persona_id).eq("scenario_id", scenario_uuid).limit(1).execute().data
    if not o or not o[0].get("step2_asset_id"):
        return None, None, None
    a = sb().table("media_assets").select("bucket,path,mime_type").eq("id", o[0]["step2_asset_id"]).limit(1).execute().data[0]
    return o[0]["id"], sb().storage.from_(a["bucket"]).download(a["path"]), (a.get("mime_type") or "image/jpeg")


def _record_qc(tenant_id: str, output_id: str, attempt: int, decision: dict,
               step2_prompt: str | None = None, image_path: str | None = None) -> None:
    avoid = "; ".join(decision.get("issues") or []) or None
    limb = decision.get("limb_description")
    try:
        sb().table("qc_checks").insert({
            "tenant_id": tenant_id, "output_id": output_id, "attempt_number": attempt,
            "qc_model": decision.get("model"), "passed": decision.get("passed"),
            "qc_reason": decision.get("recommendation"), "issues": decision.get("issues"),
            "scores": decision.get("checks"), "avoid_line": avoid,
            "limb_description": limb if isinstance(limb, str) else (json.dumps(limb) if limb else None),
            "step2_prompt": step2_prompt, "image_evaluated_path": image_path,
        }).execute()
    except Exception as e:
        print(f"   (warn) qc_checks insert failed: {e}")


def _archive_qc_fail(tenant_id, account_id, scenario_key, attempt, img, mtype) -> str | None:
    """TEMP tuning aid (migration 021): copy a QC-failed composite to the private
    'qc-failed' bucket before the next attempt overwrites .../step2.jpg on the
    pod's deterministic path. Returns the stored path, or None. Never fatal."""
    try:
        path = f"{tenant_id}/{account_id}/{scenario_key}/attempt{attempt}.jpg"
        sb().storage.from_("qc-failed").upload(
            path, img, {"content-type": mtype or "image/jpeg", "upsert": "true"})
        return path
    except Exception as e:
        print(f"   (warn) qc-failed archive upload failed: {e}")
        return None


def _set_qc_status(persona_id, scenario_uuid, status, reason, attempts):
    try:
        from datetime import datetime, timezone
        sb().table("outputs").update({"qc_status": status, "qc_reason": (reason or None), "attempts": attempts,
                                      "updated_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("persona_id", persona_id).eq("scenario_id", scenario_uuid).execute()
    except Exception as e:
        print(f"   (warn) outputs qc_status update failed: {e}")


def do_product(account, persona, scenario_uuid, scenario_key, scenario_spec, api_key) -> bool:
    """Returns True when the composite is good to continue (QC passed or QC disabled);
    False when QC failed all attempts — the caller must then SKIP realism + video and
    move to the next scenario. Terminal qc_status values: 'passed' | 'failed' only."""
    if _stage_done(persona["id"], scenario_uuid, "step2_asset_id"):
        if not QC_ENABLED:
            print("   product   ✓ (already done)"); return True
        prev = sb().table("outputs").select("qc_status").eq("persona_id", persona["id"]).eq("scenario_id", scenario_uuid).limit(1).execute().data
        prev_qc = prev[0].get("qc_status") if prev else None
        if prev_qc == "passed":
            print("   product   ✓ (already done, QC passed)"); return True
        if prev_qc in ("failed", "exhausted"):
            print("   product   ✗ (QC already failed — skipping this scenario)"); return False

    progress("step2")
    product = get_product_full(account["tenant_id"])
    step1_prompt = RS2.get_step1_prompt(scenario_key)
    base_user_msg = RS2.build_user_message(product, scenario_spec, step1_prompt)
    step2_md = (RULES_DIR / "step2_qwen.md").read_text(encoding="utf-8")
    max_retries = int(product.get("qc_max_retries") or 3) if QC_ENABLED else 0
    avoid_line = None

    for attempt in range(1, max_retries + 2):
        user_msg = base_user_msg if not avoid_line else (
            base_user_msg + f"\n\n=== AVOID (fix these specific issues found in the previous attempt) ===\n{avoid_line}")
        raw, usage = _opus(api_key, step2_md, user_msg)
        parsed = RS2.parse_json(raw)
        RS2.log_llm_call(account["tenant_id"], user_msg, raw, usage, parsed)
        step2_payload = {
            "tenant_id": account["tenant_id"], "account_id": account["id"], "persona_id": persona["id"],
            "scenario_id": scenario_uuid, "scenario_key": scenario_key, "attempt": attempt,
            "step_2_prompt": parsed["step_2_image_prompt"], "qwen_params": parsed["fal_qwen_params"]}
        if product.get("reference_angle_asset_id"):
            step2_payload["product_angle_asset_id"] = product["reference_angle_asset_id"]
        job_id = RS2.enqueue_step2(step2_payload)
        d = _dur(_ok(_poll(job_id), f"product (attempt {attempt})"))
        dtxt = f" ({d:.0f}s)" if d else ""

        if not QC_ENABLED:
            print(f"   product   ✓{dtxt}")
            return True

        progress("qc")
        output_id, img, mtype = _download_step2(persona["id"], scenario_uuid)
        decision = QC.validate(img, mtype, product, api_key, scenario_key,
                               intent=QC.placement_intent(scenario_spec))
        failed_path = None
        if not decision.get("passed") and img:
            failed_path = _archive_qc_fail(account["tenant_id"], account["id"],
                                           scenario_key, attempt, img, mtype)
        if output_id:
            _record_qc(account["tenant_id"], output_id, attempt, decision,
                       step2_prompt=parsed.get("step_2_image_prompt"), image_path=failed_path)
        if decision.get("passed"):
            _set_qc_status(persona["id"], scenario_uuid, "passed", None, attempt)
            print(f"   product   ✓{dtxt} (QC pass on attempt {attempt})")
            return True
        avoid_line = "; ".join(decision.get("issues") or []) or "regenerate with cleaner anatomy and a faithfully rendered product"
        if attempt <= max_retries:
            print(f"   product   ↻ QC fail (attempt {attempt}/{max_retries + 1}) — retrying with feedback")
        else:
            _set_qc_status(persona["id"], scenario_uuid, "failed", avoid_line[:500], attempt)
            print(f"   product   ✗ QC FAILED after {attempt} attempts — scenario marked failed; "
                  f"no realism, no video; moving on")
            return False
    return False


# web-tunable Stage-3 knobs (tenant_pipeline_config.realism_denoise /
# realism_lora_strength, migration 022) — set in main(); empty dict = pod defaults
REALISM_PARAMS: dict = {}


def do_realism(account, persona, scenario_uuid, scenario_key):
    if _stage_done(persona["id"], scenario_uuid, "step3_asset_id"):
        print("   realism   ✓ (already done)"); return
    progress("step3")
    mask_prompt = RS3.get_mask_prompt(account["tenant_id"])
    payload = {
        "tenant_id": account["tenant_id"], "account_id": account["id"], "persona_id": persona["id"],
        "scenario_id": scenario_uuid, "scenario_key": scenario_key, "mask_prompt": mask_prompt}
    if REALISM_PARAMS:
        payload["realism_params"] = REALISM_PARAMS
    job_id = RS3.enqueue_step3(payload)
    d = _dur(_ok(_poll(job_id), "realism"))
    print(f"   realism   ✓" + (f" ({d:.0f}s)" if d else ""))


# ── PHASE C: video (per finished image) ─────────────────────────────────────────
def pick_scenarios(persona_id: str, n: int) -> list:
    scenarios = sb().table("scenarios").select("id,spec").execute().data or []
    outs = {r["scenario_id"]: r for r in
            (sb().table("outputs").select("scenario_id,step2_asset_id,step3_asset_id,qc_status")
             .eq("persona_id", persona_id).execute().data or []) if r.get("scenario_id")}
    final_col = "step3_asset_id" if STEP3_ENABLED else "step2_asset_id"
    picked = []
    for s in scenarios:
        o = outs.get(s["id"], {}) or {}
        # a QC-failed scenario is CONSUMED — never re-pick it (it would retry forever)
        if o.get("qc_status") in ("failed", "exhausted"):
            continue
        if not o.get(final_col):
            picked.append(s)
        if len(picked) >= n:
            break
    return picked


def finished_images_without_video(persona_id: str, limit: int) -> list:
    finished = sb().table("outputs").select("id,scenario_id,scenario_key,persona_id,status,qc_status") \
        .eq("persona_id", persona_id).eq("status", "step3_done").execute().data or []
    # belt-and-braces: a QC-failed output never gets a video (it shouldn't reach
    # step3_done anymore, but guard against legacy/manual rows)
    finished = [o for o in finished if o.get("scenario_id")
                and o.get("qc_status") not in ("failed", "exhausted")]
    have = {v["output_id"] for v in (sb().table("videos").select("output_id").execute().data or []) if v.get("output_id")}
    todo = [o for o in finished if o["id"] not in have]
    todo.sort(key=lambda o: o.get("scenario_key") or "")
    return todo[:limit]


def do_video(account, persona, output_row, scenario_spec, api_key, sources, controls, rule_book):
    company, product, directives = sources
    scenario_key = output_row.get("scenario_key") or output_row["scenario_id"]
    progress("script")
    res = SG.generate_script(
        company_info=company, product_info=product, directives=directives,
        persona=account, scenario_key=scenario_key, scenario_spec=scenario_spec,
        num_shots=controls["num_shots"], target_seconds=controls["shot_seconds"],
        api_key=api_key, rule_book=rule_book, model=OPUS_MODEL)
    RV.log_llm_call(account["tenant_id"], res["user_message"], res["raw"], res["usage"], res["parsed"])
    payload = RV.build_enqueue_payload(account=account, persona=persona, output=output_row,
                                       controls=controls, parsed=res["parsed"])
    payload["attempt"] = 1
    progress("video")
    job_id = RV.enqueue_video(payload)
    d = _dur(_ok(_poll(job_id, VIDEO_POLL_TIMEOUT), "video"))
    print(f"   video     ✓ ({controls['video_mode']}, {res['stats']['num_shots']} shots)" + (f" ({d:.0f}s)" if d else ""))


# ── account resolution ──────────────────────────────────────────────────────────
def get_tenant(ident: str) -> dict:
    t = sb().table("tenants").select("id,slug")
    rows = (t.eq("id", ident) if RSC._UUID.match(ident) else t.eq("slug", ident)).limit(1).execute().data
    if not rows:
        raise SystemExit(f"[pipeline] no tenant matching {ident!r}")
    return rows[0]


def select_accounts_by_mode(tenant_id: str, cfg: dict) -> list:
    mode = (cfg or {}).get("creation_mode", "all")
    allacc = sb().table("tiktok_accounts").select("*").eq("tenant_id", tenant_id).execute().data or []
    if mode == "specific":
        ids = (cfg or {}).get("target_account_ids") or []
        if not ids and (cfg or {}).get("target_account_id"):
            ids = [cfg["target_account_id"]]          # back-compat with the single-id column
        ids = [i for i in ids if i]
        sel = [a for a in allacc if a["id"] in ids]
        if not sel:
            raise SystemExit(f"[pipeline] creation_mode=specific but none of {ids!r} matched this tenant's accounts")
        return sel
    if mode == "new_only":
        personas = sb().table("personas").select("tiktok_account_id").execute().data or []
        have = {p["tiktok_account_id"] for p in personas if p.get("tiktok_account_id")}
        return [a for a in allacc if a["id"] not in have]
    return allacc  # 'all'


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
        raise SystemExit("usage: run_pipeline.py <account> --scenarios N  (or --all-accounts / --accounts a,b / --tenant slug)")
    return [RSC.get_account(i) for i in idents]


class _CtlArgs:
    """Adapter so RV.resolve_controls (which expects argparse-style attrs) can read pipeline flags."""
    def __init__(self, args):
        self.mode = args.video_mode
        self.duration = args.duration
        self.shot_seconds = args.shot_seconds
        self.num_shots = args.num_shots


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("account", nargs="?")
    ap.add_argument("--accounts")
    ap.add_argument("--all-accounts", action="store_true")
    ap.add_argument("--tenant", help="run by tenant slug/id using tenant_pipeline_config (creation_mode + counts)")
    ap.add_argument("--scenarios", type=int, default=None, help="images+videos per account (default: config.num_videos_per_account)")
    ap.add_argument("--skip-videos", action="store_true", help="images only (no PHASE C)")
    ap.add_argument("--video-mode", choices=["multishot", "silentfirst"], default=None)
    ap.add_argument("--duration", type=int, default=None, help="video target seconds (overrides config)")
    ap.add_argument("--shot-seconds", type=int, default=None)
    ap.add_argument("--num-shots", type=int, default=None)
    ap.add_argument("--no-free", action="store_true")
    args = ap.parse_args()

    # accounts + tenant + config
    if args.tenant:
        tenant = get_tenant(args.tenant)
        tenant_id = tenant["id"]
        cfg = RV.get_pipeline_config(tenant_id)
        accounts = select_accounts_by_mode(tenant_id, cfg)
        if not accounts:
            raise SystemExit(f"[pipeline] creation_mode={cfg.get('creation_mode')} selected no accounts")
    else:
        accounts = resolve_accounts(args)
        tenant_id = accounts[0]["tenant_id"]
        cfg = RV.get_pipeline_config(tenant_id)

    global _TENANT_ID, STEP3_ENABLED, QC_ENABLED
    _TENANT_ID = tenant_id
    # The web Run-settings checkboxes (tenant_pipeline_config.step_3_enabled /
    # qc_enabled) drive these; the STEP_3_ENABLED / QC_ENABLED env vars stay as the
    # fallback default when the config doesn't specify. (Option A: Stage 3 off means
    # no realism AND no video — Phase C is anchored on the realism image.)
    if (cfg or {}).get("step_3_enabled") is not None:
        STEP3_ENABLED = bool(cfg["step_3_enabled"])
    if (cfg or {}).get("qc_enabled") is not None:
        QC_ENABLED = bool(cfg["qc_enabled"])
    # optional realism knobs (null = pod defaults; payload key omitted entirely)
    if (cfg or {}).get("realism_denoise") is not None:
        REALISM_PARAMS["denoise"] = float(cfg["realism_denoise"])
    if (cfg or {}).get("realism_lora_strength") is not None:
        REALISM_PARAMS["lora_strength"] = float(cfg["realism_lora_strength"])

    n_scen = args.scenarios if args.scenarios is not None else int((cfg or {}).get("num_videos_per_account") or 1)
    controls = RV.resolve_controls(cfg, _CtlArgs(args))

    # learning phase: exploration walks the curated set; active SELECTS via Thompson sampling
    state = ENG.engine_state(sb(), tenant_id)
    engine_on = bool(state.get("engine_enabled")) and state.get("phase") == "active"

    print("=" * 60)
    print("ALLUVI pipeline")
    print(f"accounts: {', '.join(a['tiktok_id'] for a in accounts)}")
    print(f"per account: {n_scen} | step3: {'on' if STEP3_ENABLED else 'off'} | QC: {'on' if QC_ENABLED else 'off'}"
          f" | video: {'off' if args.skip_videos else controls['video_mode']}")
    print(f"engine: {'ACTIVE — selecting scenarios' if engine_on else 'exploration — walking the curated set'}")
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
        prows = sb().table("personas").select("id,appearance_spec").eq("tiktok_account_id", acct["id"]).limit(1).execute().data
        if not prows:
            print(f"[{acct['tiktok_id']}] no persona — skipping (portrait may have failed)"); continue
        persona = prows[0]
        if not persona.get("appearance_spec"):
            print(f"[{acct['tiktok_id']}] persona has no appearance_spec (pre-fix portrait) — skipping; regenerate the portrait first"); continue
        api_key = RSC.get_anthropic_key(acct["tenant_id"])
        if engine_on:
            scenarios = ENG.select_scenarios(sb(), acct["tenant_id"], persona["id"], acct.get("country"), n_scen)
        else:
            scenarios = pick_scenarios(persona["id"], n_scen)
        if not scenarios:
            print(f"[{acct['tiktok_id']}] all requested scenarios already complete ✓"); continue
        for i, s in enumerate(scenarios, 1):
            spec = s.get("spec") or {}
            key = spec.get("id", s["id"])
            print(f"[{acct['tiktok_id']}] scenario {i}/{len(scenarios)}: {key}")
            do_scene(acct, persona, s["id"], key, spec, api_key)
            qc_ok = do_product(acct, persona, s["id"], key, spec, api_key)
            if not qc_ok:
                print("   scenario SKIPPED (QC failed) — counts as used; next scenario")
                continue
            if STEP3_ENABLED:
                do_realism(acct, persona, s["id"], key)
            print("   scenario complete ✓")
            total += 1

    if not args.no_free:
        print("\n--- freeing image stack (PuLID + Qwen + RealVisXL) ---")
        j = _poll(_enqueue_free(tenant_id, ["stage1", "qwen", "realism"]))
        print(f"   {'freed: PuLID, Qwen, RealVisXL ✓' if j.get('status') == 'succeeded' else '(warn) free ' + str(j.get('status'))}")

    # PHASE C — video (per finished image); image stack already freed -> VRAM for the video stack
    videos_made = 0
    if not args.skip_videos and STEP3_ENABLED:
        print(f"\n--- PHASE C: video ({controls['video_mode']}, ~{controls['video_duration_seconds']}s "
              f"-> {controls['num_shots']} shot(s) x {controls['shot_seconds']}s) ---")
        rule_book = (RULES_DIR / "script.md").read_text(encoding="utf-8")
        sources = RV.get_script_sources(tenant_id)
        for acct in accounts:
            prows = sb().table("personas").select("id").eq("tiktok_account_id", acct["id"]).limit(1).execute().data
            if not prows:
                continue
            persona = prows[0]
            api_key = RSC.get_anthropic_key(acct["tenant_id"])
            todo = finished_images_without_video(persona["id"], n_scen)
            if not todo:
                print(f"[{acct['tiktok_id']}] no finished images awaiting a video ✓"); continue
            for j, orow in enumerate(todo, 1):
                key = orow.get("scenario_key") or orow["scenario_id"]
                print(f"[{acct['tiktok_id']}] video {j}/{len(todo)}: {key}")
                spec = RV.get_scenario_spec(orow["scenario_id"])
                try:
                    do_video(acct, persona, orow, spec, api_key, sources, controls, rule_book)
                    videos_made += 1
                except Exception as e:
                    print(f"   video     ✗ FAILED: {type(e).__name__}: {e}")
        if videos_made and not args.no_free:
            print("\n--- freeing video stack (Wan + F5 + LatentSync) ---")
            j = _poll(_enqueue_free(tenant_id, ["video"]))
            print(f"   {'freed: video stack ✓' if j.get('status') == 'succeeded' else '(warn) free ' + str(j.get('status'))}")

    # lifecycle: turn the engine ON automatically once the curated set is worked through
    # (succeeded OR QC-skipped). Idempotent; never flips back.
    flip = ENG.maybe_flip_engine(sb(), tenant_id)
    prog = (f"{flip.get('resolved')}/{flip.get('active_curated')}"
            if flip.get("active_curated") is not None else "?")
    if flip.get("flipped"):
        print("\n" + "*" * 60)
        print(f"ENGINE ACTIVATED  curated set complete ({prog}) — future runs SELECT via Thompson sampling.")
        print("*" * 60)
    elif flip.get("phase") == "active":
        print(f"\n[engine] active — selecting ({prog} curated resolved)")
    else:
        print(f"\n[engine] exploration — coverage {flip.get('pct_complete', 0)}% ({prog} curated resolved)")

    print("\n" + "=" * 60)
    print(f"PIPELINE COMPLETE ✓   ({total} image(s), {videos_made} video(s))")
    print("=" * 60)


if __name__ == "__main__":
    main()