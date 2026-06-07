"""
orchestrator/run_scene.py — LOCAL "brain" for Stage 1 (scene).

DB-driven DATA, repo-FILE rules:
  * persona descriptors  <- personas.appearance_spec  (the verbatim prompt_descriptors)
  * account gender       <- tiktok_accounts.gender     (picks the gendered outfit)
  * scenario             <- scenarios.spec             (scene/outfit/pose/lighting/...)
  * Step-1 rule book     <- rules/step1.md
  * Anthropic key        <- Vault via get_tenant_anthropic_key()
No brand/product at this stage. Opus builds the Step-1 PuLID prompt -> logged to
llm_calls -> enqueued on the gateway (/scene) -> polled until the worker finishes.

RUN:
  python run_scene.py @liam.foster gym_post_workout_mirror_01      # specific scenario (by key)
  python run_scene.py @liam.foster next                            # first not-yet-done scenario
  python run_scene.py @liam.foster next --dry-run                  # build + print prompt, NO GPU
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from supabase import create_client, Client

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

OPUS_MODEL = os.environ.get("OPUS_MODEL", "claude-opus-4-7")
RULES_DIR = Path(os.environ.get("RULES_DIR", HERE / "rules"))
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


# ── DB reads ──────────────────────────────────────────────────────────────────
def get_account(ident: str) -> dict:
    t = sb().table("tiktok_accounts").select("*")
    rows = (t.eq("id", ident) if _UUID.match(ident) else t.eq("tiktok_id", ident)).limit(1).execute().data
    if not rows and not _UUID.match(ident) and not ident.startswith("@"):
        rows = sb().table("tiktok_accounts").select("*").eq("tiktok_id", "@" + ident).limit(1).execute().data
    if not rows:
        sys.exit(f"[scene] no account matching {ident!r}")
    return rows[0]


def get_persona(account_id: str) -> dict:
    rows = sb().table("personas").select("*").eq("tiktok_account_id", account_id).limit(1).execute().data
    if not rows:
        sys.exit("[scene] no persona for this account — run run_portrait.py first")
    persona = rows[0]
    if not persona.get("appearance_spec"):
        sys.exit("[scene] persona has no appearance_spec (pre-fix portrait) — regenerate the portrait first")
    return persona


def pick_scenario(persona_id: str, selector: str) -> dict:
    scenarios = sb().table("scenarios").select("id,spec").execute().data or []
    by_key = {(s.get("spec") or {}).get("id"): s for s in scenarios}
    if selector and selector != "next":
        s = by_key.get(selector)
        if not s:
            sys.exit(f"[scene] no scenario with key {selector!r}. Available: {sorted(k for k in by_key if k)[:5]}...")
        return s
    done = {r["scenario_id"] for r in (sb().table("outputs").select("scenario_id")
            .eq("persona_id", persona_id).execute().data or []) if r.get("scenario_id")}
    for s in scenarios:
        if s["id"] not in done:
            return s
    sys.exit("[scene] all scenarios already done for this persona")


def get_anthropic_key(tenant_id: str) -> str:
    key = sb().rpc("get_tenant_anthropic_key", {"p_tenant_id": tenant_id}).execute().data
    if not key:
        sys.exit("[scene] no Anthropic key in Vault for this tenant")
    return key


# ── prompt build ────────────────────────────────────────────────────────────────
def _gender_key(gender: str | None) -> str:
    g = (gender or "").strip().lower()
    return "male" if g.startswith("m") else "female"


def build_user_message(persona: dict, gender: str, scenario_spec: dict) -> str:
    descriptors = (json.loads(persona["appearance_spec"]) or {}).get("prompt_descriptors") or {}
    # resolve the gendered outfit so the model gets the exact outfit string
    spec = dict(scenario_spec)
    outfit = spec.get("outfit")
    if isinstance(outfit, dict):
        spec["outfit"] = outfit.get(_gender_key(gender)) or next(iter(outfit.values()), "")
    return "\n".join([
        "=== PERSONA prompt_descriptors (COPY THE CHOSEN LINES VERBATIM) ===",
        json.dumps(descriptors, indent=2, ensure_ascii=False),
        "",
        f"=== PERSONA gender === {gender}",
        "",
        "=== SCENARIO (build the Step-1 scene from this; outfit already resolved to gender) ===",
        json.dumps(spec, indent=2, ensure_ascii=False),
        "",
        "=== TASK ===",
        "Output exactly ONE Step-1 JSON envelope per the system prompt. JSON only, no fences, no preamble.",
    ])


def parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t).rsplit("```", 1)[0]
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("no JSON object in model output")
    obj = json.loads(t[i:j + 1])
    for k in ("step_1_image_prompt", "fal_pulid_params"):
        if not obj.get(k):
            raise ValueError(f"model output missing required key: {k}")
    return obj


def log_llm_call(tenant_id: str, user_message: str, raw: str, usage: dict, parsed: dict) -> None:
    try:
        sb().table("llm_calls").insert({
            "tenant_id": tenant_id, "purpose": "step1_prompt", "model": OPUS_MODEL,
            "system_prompt_name": "step1", "system_prompt_version": "v1",
            "user_message": user_message, "raw_response": raw, "parsed_json": parsed,
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        }).execute()
    except Exception as e:
        print(f"[scene] (warn) llm_calls log failed: {e}")


# ── gateway ─────────────────────────────────────────────────────────────────────
def enqueue_scene(payload: dict) -> str:
    r = requests.post(f"{GATEWAY_URL}/scene",
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
        print(f"[scene]   job {job_id} -> {job.get('status')}")
        if job.get("status") in ("succeeded", "failed"):
            return job
        time.sleep(POLL_SECONDS)
    return {"status": "timeout", "job_id": job_id}


# ── main ─────────────────────────────────────────────────────────────────────────
def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    if len(args) < 2:
        sys.exit('usage: python run_scene.py <account> <scenario_key|next> [--dry-run]')
    account = get_account(args[0])
    tenant_id = account["tenant_id"]
    persona = get_persona(account["id"])
    scenario = pick_scenario(persona["id"], args[1])
    spec = scenario.get("spec") or {}
    scenario_key = spec.get("id", scenario["id"])
    print(f"[scene] account={account['tiktok_id']} gender={account.get('gender')} scenario={scenario_key}")

    api_key = get_anthropic_key(tenant_id)
    user_message = build_user_message(persona, account.get("gender"), spec)
    print(f"[scene] Step 1 -> {OPUS_MODEL}")
    resp = Anthropic(api_key=api_key).messages.create(
        model=OPUS_MODEL, max_tokens=4096,
        system=(RULES_DIR / "step1.md").read_text(encoding="utf-8"),
        messages=[{"role": "user", "content": user_message}],
    )
    raw = resp.content[0].text
    parsed = parse_json(raw)
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    log_llm_call(tenant_id, user_message, raw, usage, parsed)

    step_1_prompt = parsed["step_1_image_prompt"]
    pulid_params = parsed["fal_pulid_params"]
    print(f"\n[scene] step_1_image_prompt ({parsed.get('word_count','?')} words):\n{step_1_prompt}\n")
    print(f"[scene] id_weight={pulid_params.get('id_weight')} true_cfg={pulid_params.get('true_cfg')} "
          f"size={pulid_params.get('image_size')}\n")

    if dry_run:
        print("[scene] --dry-run: not enqueued. Review the prompt above, then re-run without --dry-run.")
        return

    job_id = enqueue_scene({
        "tenant_id": tenant_id, "account_id": account["id"], "persona_id": persona["id"],
        "scenario_id": scenario["id"], "scenario_key": scenario_key,
        "step_1_prompt": step_1_prompt, "pulid_params": pulid_params,
    })
    print(f"[scene] enqueued job {job_id}; polling…")
    final = poll(job_id)
    print(f"\n[scene] FINAL: {json.dumps(final, indent=2)}")


if __name__ == "__main__":
    main()
