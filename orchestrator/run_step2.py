"""
orchestrator/run_step2.py — LOCAL "brain" for Step 2 (product composite, Qwen).

DB-driven DATA, repo-FILE rules:
  * product packaging  <- products.packaging (text_on_packaging quoted VERBATIM by Opus)
  * scenario           <- scenarios.spec      (archetype/grip_or_placement/pose/lighting/scene)
  * Step-1 prompt      <- llm_calls (purpose=step1_prompt, parsed_json.scenario_id == key) for the lighting echo
  * Step-2 rule book   <- rules/step2_qwen.md
The persona is NOT passed as text — image #1 (the Step-1 scene) carries it.
Opus builds the Qwen edit prompt -> logged to llm_calls -> enqueued (/step2) -> polled.

RUN:
  python run_step2.py @liam.foster gym_post_workout_mirror_01            # specific (must have step1 done)
  python run_step2.py @liam.foster next                                  # first scene with step1 done, step2 not
  python run_step2.py @liam.foster gym_post_workout_mirror_01 --dry-run  # build + print prompt, NO GPU
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


def get_account(ident: str) -> dict:
    t = sb().table("tiktok_accounts").select("*")
    rows = (t.eq("id", ident) if _UUID.match(ident) else t.eq("tiktok_id", ident)).limit(1).execute().data
    if not rows and not _UUID.match(ident) and not ident.startswith("@"):
        rows = sb().table("tiktok_accounts").select("*").eq("tiktok_id", "@" + ident).limit(1).execute().data
    if not rows:
        sys.exit(f"[step2] no account matching {ident!r}")
    return rows[0]


def get_persona(account_id: str) -> dict:
    rows = sb().table("personas").select("id").eq("tiktok_account_id", account_id).limit(1).execute().data
    if not rows:
        sys.exit("[step2] no persona for this account")
    return rows[0]


def pick_output(persona_id: str, selector: str) -> dict:
    """Return an outputs row that HAS step1 done (so a scene exists to composite onto)."""
    rows = sb().table("outputs").select("scenario_id,scenario_key,step1_asset_id,step2_asset_id") \
        .eq("persona_id", persona_id).execute().data or []
    have_step1 = [r for r in rows if r.get("step1_asset_id")]
    if selector and selector != "next":
        for r in have_step1:
            if r.get("scenario_key") == selector:
                return r
        sys.exit(f"[step2] no step1-done output for scenario {selector!r}. Run the scene first.")
    for r in have_step1:
        if not r.get("step2_asset_id"):
            return r
    sys.exit("[step2] no scene with step1 done and step2 pending for this persona")


def get_product(tenant_id: str) -> dict:
    rows = sb().table("products").select("name,packaging,reference_asset_id").eq("tenant_id", tenant_id).limit(1).execute().data
    if not rows:
        sys.exit("[step2] no product for this tenant")
    if not rows[0].get("reference_asset_id"):
        sys.exit("[step2] product has no reference photo (reference_asset_id null) — register it first")
    return rows[0]


def get_scenario_spec(scenario_id: str) -> dict:
    rows = sb().table("scenarios").select("spec").eq("id", scenario_id).limit(1).execute().data
    return (rows[0].get("spec") if rows else {}) or {}


def get_step1_prompt(scenario_key: str) -> str | None:
    rows = sb().table("llm_calls").select("parsed_json") \
        .eq("purpose", "step1_prompt").eq("parsed_json->>scenario_id", scenario_key) \
        .order("created_at", desc=True).limit(1).execute().data
    if rows and rows[0].get("parsed_json"):
        return (rows[0]["parsed_json"] or {}).get("step_1_image_prompt")
    return None


def get_anthropic_key(tenant_id: str) -> str:
    key = sb().rpc("get_tenant_anthropic_key", {"p_tenant_id": tenant_id}).execute().data
    if not key:
        sys.exit("[step2] no Anthropic key in Vault for this tenant")
    return key


def build_user_message(product: dict, scenario_spec: dict, step1_prompt: str | None) -> str:
    return "\n".join([
        "=== PRODUCT packaging data (quote text_on_packaging VERBATIM) ===",
        json.dumps({"name": product.get("name"), "packaging": product.get("packaging")}, indent=2, ensure_ascii=False),
        "",
        "=== SCENARIO ===",
        json.dumps(scenario_spec, indent=2, ensure_ascii=False),
        "",
        "=== STEP 1 OUTPUT PROMPT (read the HAND POSITIONS from it: if the intended product hand is occupied, crossed, or away from the target position, author the explicit arm re-pose per the rule book; compress its lighting into the one-line LIGHTING section) ===",
        step1_prompt or "(step-1 prompt unavailable — derive lighting from scenario.lighting; assume the product hand may need an explicit re-pose)",
        "",
        "=== TASK ===",
        "Output exactly ONE Step-2 JSON envelope per the system prompt. JSON only, no fences, no preamble.",
    ])


def parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t).rsplit("```", 1)[0]
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("no JSON object in model output")
    obj = json.loads(t[i:j + 1])
    for k in ("step_2_image_prompt", "fal_qwen_params"):
        if not obj.get(k):
            raise ValueError(f"model output missing required key: {k}")
    return obj


def log_llm_call(tenant_id: str, user_message: str, raw: str, usage: dict, parsed: dict) -> None:
    try:
        sb().table("llm_calls").insert({
            "tenant_id": tenant_id, "purpose": "step2_prompt", "model": OPUS_MODEL,
            "system_prompt_name": "step2_qwen", "system_prompt_version": "v1",
            "user_message": user_message, "raw_response": raw, "parsed_json": parsed,
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        }).execute()
    except Exception as e:
        print(f"[step2] (warn) llm_calls log failed: {e}")


def enqueue_step2(payload: dict) -> str:
    r = requests.post(f"{GATEWAY_URL}/step2",
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
        print(f"[step2]   job {job_id} -> {job.get('status')}")
        if job.get("status") in ("succeeded", "failed"):
            return job
        time.sleep(POLL_SECONDS)
    return {"status": "timeout", "job_id": job_id}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    if len(args) < 2:
        sys.exit('usage: python run_step2.py <account> <scenario_key|next> [--dry-run]')
    account = get_account(args[0])
    tenant_id = account["tenant_id"]
    persona = get_persona(account["id"])
    out_row = pick_output(persona["id"], args[1])
    scenario_id = out_row["scenario_id"]
    scenario_key = out_row.get("scenario_key") or scenario_id
    print(f"[step2] account={account['tiktok_id']} scenario={scenario_key}")

    product = get_product(tenant_id)
    scenario_spec = get_scenario_spec(scenario_id)
    step1_prompt = get_step1_prompt(scenario_key)
    if not step1_prompt:
        print("[step2] (note) step-1 prompt not found in llm_calls — lighting will derive from scenario.lighting")

    api_key = get_anthropic_key(tenant_id)
    user_message = build_user_message(product, scenario_spec, step1_prompt)
    print(f"[step2] Step 2 -> {OPUS_MODEL}")
    resp = Anthropic(api_key=api_key).messages.create(
        model=OPUS_MODEL, max_tokens=4096,
        system=(RULES_DIR / "step2_qwen.md").read_text(encoding="utf-8"),
        messages=[{"role": "user", "content": user_message}],
    )
    raw = resp.content[0].text
    parsed = parse_json(raw)
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    log_llm_call(tenant_id, user_message, raw, usage, parsed)

    step_2_prompt = parsed["step_2_image_prompt"]
    qwen_params = parsed["fal_qwen_params"]
    print(f"\n[step2] step_2_image_prompt ({parsed.get('word_count','?')} words):\n{step_2_prompt}\n")
    print(f"[step2] qwen_params: {json.dumps(qwen_params)}\n")

    if dry_run:
        print("[step2] --dry-run: not enqueued. Review the prompt above, then re-run without --dry-run.")
        return

    job_id = enqueue_step2({
        "tenant_id": tenant_id, "account_id": account["id"], "persona_id": persona["id"],
        "scenario_id": scenario_id, "scenario_key": scenario_key,
        "step_2_prompt": step_2_prompt, "qwen_params": qwen_params,
    })
    print(f"[step2] enqueued job {job_id}; polling…")
    final = poll(job_id)
    print(f"\n[step2] FINAL: {json.dumps(final, indent=2)}")


if __name__ == "__main__":
    main()
