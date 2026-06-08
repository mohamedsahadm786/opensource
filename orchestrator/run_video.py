"""
orchestrator/run_video.py — LOCAL "brain" for the VIDEO stage (script + enqueue + poll).

DB-driven DATA, repo-FILE rule book:
  * finished image      <- outputs (status='step3_done').step3_asset_id  (the realism photo)
  * persona fields      <- tiktok_accounts (name/gender/country/language/age)
  * scene               <- scenarios.spec
  * BRAND KNOWLEDGE     <- tenants.script_company_info
  * PRODUCT KNOWLEDGE   <- products.product_info
  * SCRIPT DIRECTIVES   <- tenants.script_directives
  * run controls        <- tenant_pipeline_config   (video_mode, duration, shot_seconds, knobs)
  * Script rule book    <- rules/script.md
  * Anthropic key       <- Vault via get_tenant_anthropic_key()

Opus builds the multi-shot script -> logged to llm_calls -> enqueued on the gateway (/video)
-> polled until the worker finishes. The worker downloads the step3 image, calls the
video-service (pure GPU assembly), and writes the videos / media_generations rows.

RUN:
  python run_video.py @liam.foster gym_post_workout_mirror_01           # that scenario's image
  python run_video.py @liam.foster next                                 # first finished image w/o a video
  python run_video.py @liam.foster next --mode silentfirst --duration 20
  python run_video.py @liam.foster next --dry-run                       # build + print script, NO GPU
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from supabase import create_client, Client

import script_gen as SG

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

OPUS_MODEL = os.environ.get("OPUS_MODEL", "claude-opus-4-7")
RULES_DIR = Path(os.environ.get("RULES_DIR", HERE / "rules"))
GATEWAY_URL = os.environ["GATEWAY_URL"].rstrip("/")
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "10"))
POLL_TIMEOUT = int(os.environ.get("VIDEO_POLL_TIMEOUT_S", "5400"))  # video is slow

_sb: Client | None = None
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# tenant_pipeline_config defaults (used if the tenant has no row yet)
CFG_DEFAULTS = {
    "video_mode": "multishot", "video_duration_seconds": 10, "shot_seconds": 5,
    "intro_seconds": 2, "outro_seconds": 2, "tail_seconds": 0,
    "lips_expression": 2.0, "inference_steps": 40, "punch_in": 1.2, "threshold": 70, "seed": None,
}


def sb() -> Client:
    global _sb
    if _sb is None:
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    return _sb


# ── DB reads ──────────────────────────────────────────────────────────────────
def get_account(ident: str) -> dict:
    t = sb().table("tiktok_accounts").select("*")
    rows = (t.eq("id", ident) if _UUID.match(ident) else t.eq("tiktok_id", ident)).limit(1).execute().data
    if not rows and not _UUID.match(ident) and not ident.startswith("@"):
        rows = sb().table("tiktok_accounts").select("*").eq("tiktok_id", "@" + ident).limit(1).execute().data
    if not rows:
        sys.exit(f"[video] no account matching {ident!r}")
    return rows[0]


def get_persona(account_id: str) -> dict:
    rows = sb().table("personas").select("*").eq("tiktok_account_id", account_id).limit(1).execute().data
    if not rows:
        sys.exit("[video] no persona for this account — run the image pipeline first")
    return rows[0]


def pick_finished_output(persona_id: str, selector: str, output_id: str | None) -> dict:
    """A finished realism image (status='step3_done') to animate."""
    q = sb().table("outputs").select("*").eq("persona_id", persona_id).eq("status", "step3_done")
    rows = q.execute().data or []
    rows = [r for r in rows if r.get("step3_asset_id")]
    if output_id:
        rows = [r for r in rows if r["id"] == output_id]
        if not rows:
            sys.exit(f"[video] no finished (step3_done) output with id {output_id}")
        return rows[0]
    if selector and selector != "next":
        rows = [r for r in rows if r.get("scenario_key") == selector]
        if not rows:
            sys.exit(f"[video] no finished image for scenario {selector!r} (run the image pipeline first)")
        return rows[0]
    have = {v["output_id"] for v in (sb().table("videos").select("output_id").execute().data or []) if v.get("output_id")}
    todo = [r for r in rows if r["id"] not in have]
    if not todo:
        sys.exit("[video] no finished images awaiting a video for this persona")
    return sorted(todo, key=lambda r: r.get("scenario_key") or "")[0]


def get_scenario_spec(scenario_id: str) -> dict:
    rows = sb().table("scenarios").select("spec").eq("id", scenario_id).limit(1).execute().data
    return (rows[0].get("spec") or {}) if rows else {}


def get_pipeline_config(tenant_id: str) -> dict:
    rows = sb().table("tenant_pipeline_config").select("*").eq("tenant_id", tenant_id).limit(1).execute().data
    return rows[0] if rows else {}


def get_script_sources(tenant_id: str) -> tuple[dict, dict, dict]:
    t = sb().table("tenants").select("script_company_info,script_directives").eq("id", tenant_id).limit(1).execute().data
    trow = t[0] if t else {}
    p = sb().table("products").select("product_info").eq("tenant_id", tenant_id).limit(1).execute().data
    prow = p[0] if p else {}
    return (trow.get("script_company_info") or {}, prow.get("product_info") or {},
            trow.get("script_directives") or {})


def get_anthropic_key(tenant_id: str) -> str:
    key = sb().rpc("get_tenant_anthropic_key", {"p_tenant_id": tenant_id}).execute().data
    if not key:
        sys.exit("[video] no Anthropic key in Vault for this tenant")
    return key


# ── control resolution (pure / testable) ────────────────────────────────────────
def resolve_controls(cfg: dict, args: argparse.Namespace) -> dict:
    """Merge tenant_pipeline_config with CLI overrides; derive num_shots from duration."""
    c = {**CFG_DEFAULTS, **{k: v for k, v in (cfg or {}).items() if v is not None}}
    mode = args.mode or c["video_mode"]
    shot_seconds = args.shot_seconds or int(c["shot_seconds"])
    duration = args.duration or int(c["video_duration_seconds"])
    num_shots = args.num_shots or max(1, math.ceil(duration / max(1, shot_seconds)))
    return {
        "video_mode": mode, "shot_seconds": shot_seconds, "num_shots": num_shots,
        "video_duration_seconds": duration,
        "intro_seconds": float(c["intro_seconds"]), "outro_seconds": float(c["outro_seconds"]),
        "tail_seconds": float(c["tail_seconds"]), "lips_expression": float(c["lips_expression"]),
        "inference_steps": int(c["inference_steps"]), "punch_in": float(c["punch_in"]),
        "threshold": int(c["threshold"]), "seed": c.get("seed"),
    }


def build_enqueue_payload(*, account: dict, persona: dict, output: dict, controls: dict,
                          parsed: dict) -> dict:
    return {
        "tenant_id": account["tenant_id"], "account_id": account["id"],
        "persona_id": persona["id"], "output_id": output["id"],
        "scenario_id": output["scenario_id"], "scenario_key": output.get("scenario_key"),
        "gender": account.get("gender"),
        "shots": parsed["shots"],
        "narrative_theme": parsed.get("narrative_theme"), "language": parsed.get("language"),
        "hook_style": parsed.get("hook_style"), "scene_mood": parsed.get("scene_mood"),
        **controls,
    }


def log_llm_call(tenant_id: str, user_message: str, raw: str, usage: dict, parsed: dict) -> None:
    try:
        sb().table("llm_calls").insert({
            "tenant_id": tenant_id, "purpose": "video_script", "model": OPUS_MODEL,
            "system_prompt_name": "script", "system_prompt_version": "v1",
            "user_message": user_message, "raw_response": raw, "parsed_json": parsed,
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        }).execute()
    except Exception as e:
        print(f"[video] (warn) llm_calls log failed: {e}")


# ── gateway ─────────────────────────────────────────────────────────────────────
def enqueue_video(payload: dict) -> str:
    r = requests.post(f"{GATEWAY_URL}/video",
                      headers={"X-API-Key": GATEWAY_API_KEY, "Content-Type": "application/json"},
                      json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["job_id"]


def poll(job_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = requests.get(f"{GATEWAY_URL}/jobs/{job_id}", headers={"X-API-Key": GATEWAY_API_KEY}, timeout=30)
        r.raise_for_status()
        job = r.json()
        print(f"[video]   job {job_id} -> {job.get('status')}")
        if job.get("status") in ("succeeded", "failed"):
            return job
        time.sleep(POLL_SECONDS)
    return {"status": "timeout", "job_id": job_id}


# ── main ─────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="ALLUVI video stage (script -> enqueue -> poll)")
    ap.add_argument("account")
    ap.add_argument("scenario", nargs="?", default="next", help="scenario_key | 'next'")
    ap.add_argument("--output-id", default=None)
    ap.add_argument("--mode", choices=["multishot", "silentfirst"], default=None)
    ap.add_argument("--duration", type=int, default=None, help="target seconds (overrides config)")
    ap.add_argument("--shot-seconds", type=int, default=None)
    ap.add_argument("--num-shots", type=int, default=None, help="explicit shot count (overrides duration)")
    ap.add_argument("--attempt", type=int, default=1, help="bump to force a fresh run for same output+mode")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    account = get_account(args.account)
    tenant_id = account["tenant_id"]
    persona = get_persona(account["id"])
    output = pick_finished_output(persona["id"], args.scenario, args.output_id)
    scenario_key = output.get("scenario_key") or output["scenario_id"]
    scenario_spec = get_scenario_spec(output["scenario_id"])

    cfg = get_pipeline_config(tenant_id)
    controls = resolve_controls(cfg, args)
    company_info, product_info, directives = get_script_sources(tenant_id)

    print(f"[video] account={account['tiktok_id']} gender={account.get('gender')} scenario={scenario_key} "
          f"output={output['id']}")
    print(f"[video] mode={controls['video_mode']} duration~{controls['video_duration_seconds']}s "
          f"-> {controls['num_shots']} shot(s) x {controls['shot_seconds']}s")

    api_key = get_anthropic_key(tenant_id)
    print(f"[video] script -> {OPUS_MODEL}")
    res = SG.generate_script(
        company_info=company_info, product_info=product_info, directives=directives,
        persona=account, scenario_key=scenario_key, scenario_spec=scenario_spec,
        num_shots=controls["num_shots"], target_seconds=controls["shot_seconds"],
        api_key=api_key, rule_book=(RULES_DIR / "script.md").read_text(encoding="utf-8"),
        model=OPUS_MODEL)
    parsed, stats = res["parsed"], res["stats"]
    log_llm_call(tenant_id, res["user_message"], res["raw"], res["usage"], parsed)

    print(f"\n[video] narrative: {parsed.get('narrative_theme')}  | language: {parsed.get('language')}")
    print(f"[video] {stats['num_shots']} shots, {stats['total_words']}w (~{stats['est_speech_seconds']}s speech)"
          f"{'  [shot count != requested]' if not stats['shot_count_matches'] else ''}")
    for i, sh in enumerate(parsed["shots"]):
        print(f"\n  shot {i+1} ({sh.get('_dialogue_word_count','?')}w): {sh['dialogue']}")
        print(f"    motion: {sh['wan_motion_prompt'][:110]}...")

    if args.dry_run:
        print("\n[video] --dry-run: not enqueued. Review the script above, then re-run without --dry-run.")
        return

    payload = build_enqueue_payload(account=account, persona=persona, output=output,
                                    controls=controls, parsed=parsed)
    payload["attempt"] = args.attempt
    job_id = enqueue_video(payload)
    print(f"\n[video] enqueued job {job_id}; polling (video is slow)…")
    final = poll(job_id)
    print(f"\n[video] FINAL: {json.dumps(final, indent=2)}")


if __name__ == "__main__":
    main()
    