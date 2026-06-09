"""
orchestrator/run_portrait.py — LOCAL "brain" for Phase A (portrait).

DB-driven DATA, repo-FILE rules:
  * account identity      <- tiktok_accounts          (gender, country, age, language, name)
  * Phase-A rule book     <- rules/phaseA.md          (repo file — tune per stage here)
  * Anthropic key         <- Vault via get_tenant_anthropic_key()
Portrait uses ONLY account identity — no brand, no product, no do_dont.
Then: Opus builds the portrait prompt -> logged to llm_calls -> enqueued on the
gateway (/portrait) -> polled until the worker finishes.

SETUP (once):
  cd orchestrator
  cp .env.example .env        # fill SUPABASE_*, GATEWAY_URL, GATEWAY_API_KEY
  pip install anthropic supabase python-dotenv requests

RUN:
  python run_portrait.py @liam.foster          # by tiktok handle
  python run_portrait.py 29dc69c1-...-c606a4    # or by account uuid
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
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT_S", "600"))

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
    if _UUID.match(ident):
        rows = t.eq("id", ident).limit(1).execute().data
    else:
        rows = t.eq("tiktok_id", ident).limit(1).execute().data
        if not rows and not ident.startswith("@"):
            rows = sb().table("tiktok_accounts").select("*").eq("tiktok_id", "@" + ident).limit(1).execute().data
    if not rows:
        sys.exit(f"[orchestrator] no account matching {ident!r}")
    return rows[0]


def get_anthropic_key(tenant_id: str) -> str:
    key = sb().rpc("get_tenant_anthropic_key", {"p_tenant_id": tenant_id}).execute().data
    if not key:
        sys.exit("[orchestrator] no Anthropic key in Vault for this tenant (check anthropic_secret_name)")
    return key


# ── prompt build (account identity ONLY) ────────────────────────────────────────
TASK_BLOCK = "\n".join([
    "=== TASK ===",
    "Generate this persona's PERMANENT visual identity per the system prompt.",
    "",
    "HARD REQUIREMENTS:",
    "  - gender_presentation MUST match the account's gender; use the matching",
    "    pronoun (she/her or he/him) in EVERY descriptor and identity-lock line.",
    "  - apparent age tracks the account age but NEVER below 21 (compliance floor).",
    "  - features realistically and respectfully consistent with the country;",
    "    a specific believable person, not an ethnically-ambiguous model.",
    "  - the 5 prompt_descriptors fields are MANDATORY and gender-correct.",
    "  - portrait_prompt: a 60-110 word FRONT-FACING neutral reference headshot,",
    "    plain background, neutral closed-mouth expression, NO product/props/text,",
    "    with the photoreal open/close anchors. Gender-correct pronoun.",
    "  - body proportions believable and natural — never emaciated, never a minor.",
    "    Body type itself is FREE (lean | average | soft | stocky | heavier/plus-size).",
    "    If 'appearance_request' is provided below, treat it as an AUTHORITATIVE",
    "    override for body type / build / face shape (e.g. heavier/fat build, round",
    "    face) — render a REAL believable person of that build (21+, not cartoonish),",
    "    and carry it into body_type, body_proportions, face.shape and the descriptors.",
    "",
    "Output JSON only. No preamble. No markdown fences.",
])


def _appearance_note(account: dict) -> str:
    """Optional plain-English appearance/build request from the web account form
    (tiktok_accounts.identity_factors). Empty -> no request (default behaviour)."""
    idf = account.get("identity_factors")
    if isinstance(idf, dict):
        return (idf.get("appearance") or "").strip()
    if isinstance(idf, str):
        return idf.strip()
    return ""


def build_user_message(account: dict) -> str:
    ident = {
        "tiktok_id": account.get("tiktok_id"),
        "name": account.get("name"),
        "gender": account.get("gender"),
        "country": account.get("country"),
        "age": account.get("age"),
        "language": account.get("language"),
    }
    note = _appearance_note(account)
    if note:
        ident["appearance_request"] = note   # authoritative appearance/build override
    return "\n".join([
        "=== ACCOUNT IDENTITY FACTORS (generate the persona for THIS person) ===",
        json.dumps(ident, indent=2, ensure_ascii=False),
        "",
        TASK_BLOCK,
    ])


def parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t).rsplit("```", 1)[0]
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("no JSON object in model output")
    obj = json.loads(t[i:j + 1])
    for k in ("portrait_prompt", "prompt_descriptors"):
        if not obj.get(k):
            raise ValueError(f"model output missing required key: {k}")
    return obj


def build_portrait_prompt(account: dict, system_prompt: str, api_key: str):
    user_message = build_user_message(account)
    print(f"[orchestrator] Phase A -> {OPUS_MODEL} for {account.get('tiktok_id')}")
    resp = Anthropic(api_key=api_key).messages.create(
        model=OPUS_MODEL, max_tokens=4096, system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = resp.content[0].text
    parsed = parse_json(raw)
    meta = {"raw": raw, "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return parsed, user_message, meta


def log_llm_call(tenant_id: str, user_message: str, meta: dict, parsed: dict) -> None:
    try:
        sb().table("llm_calls").insert({
            "tenant_id": tenant_id, "purpose": "phasea_prompt", "model": OPUS_MODEL,
            "system_prompt_name": "phaseA", "system_prompt_version": "v1",
            "user_message": user_message, "raw_response": meta["raw"], "parsed_json": parsed,
            "input_tokens": meta["input_tokens"], "output_tokens": meta["output_tokens"],
        }).execute()
    except Exception as e:  # logging must never break the run
        print(f"[orchestrator] (warn) llm_calls log failed: {e}")


# ── gateway ─────────────────────────────────────────────────────────────────────
def enqueue_portrait(tenant_id: str, account_id: str, portrait_prompt: str, appearance_spec: dict) -> str:
    r = requests.post(
        f"{GATEWAY_URL}/portrait",
        headers={"X-API-Key": GATEWAY_API_KEY, "Content-Type": "application/json"},
        json={"tenant_id": tenant_id, "account_id": account_id, "portrait_prompt": portrait_prompt, "appearance_spec": appearance_spec},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["job_id"]


def poll(job_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = requests.get(f"{GATEWAY_URL}/jobs/{job_id}", headers={"X-API-Key": GATEWAY_API_KEY}, timeout=30)
        r.raise_for_status()
        job = r.json()
        print(f"[orchestrator]   job {job_id} -> {job.get('status')}")
        if job.get("status") in ("succeeded", "failed"):
            return job
        time.sleep(POLL_SECONDS)
    return {"status": "timeout", "job_id": job_id}


# ── main ─────────────────────────────────────────────────────────────────────────
def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python run_portrait.py <tiktok_handle|account_uuid>")
    system_prompt = (RULES_DIR / "phaseA.md").read_text(encoding="utf-8")

    account = get_account(sys.argv[1])
    tenant_id = account["tenant_id"]
    api_key = get_anthropic_key(tenant_id)

    parsed, user_message, meta = build_portrait_prompt(account, system_prompt, api_key)
    log_llm_call(tenant_id, user_message, meta, parsed)
    portrait_prompt = parsed["portrait_prompt"]
    print("\n[orchestrator] portrait_prompt:\n" + portrait_prompt + "\n")

    job_id = enqueue_portrait(tenant_id, account["id"], portrait_prompt, parsed)
    print(f"[orchestrator] enqueued job {job_id}; polling…")
    final = poll(job_id)
    print(f"\n[orchestrator] FINAL: {json.dumps(final, indent=2)}")


if __name__ == "__main__":
    main()