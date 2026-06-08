"""
orchestrator/run_tuning.py — drive the L4 tuning loop.

  python run_tuning.py propose  --tenant alluvi          # credit-assign + Opus hypotheses -> candidates
  python run_tuning.py list     --tenant alluvi          # show suggestions by status
  python run_tuning.py promote  --tenant alluvi --id <suggestion_id>   # candidate -> testing (captures baseline)
  python run_tuning.py promote  --tenant alluvi --all    # promote every candidate
  python run_tuning.py validate --tenant alluvi          # testing -> validated/rejected via pre/post A/B

Only 'testing' + 'validated' edits are returned by tuning.get_active_edits (the generation hook),
so candidates never affect output until you promote them, and never stick until they validate.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from anthropic import Anthropic

import run_scene as RSC          # shared sb() + get_anthropic_key + _UUID
import engine as ENG
import tuning as T

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
OPUS_MODEL = os.environ.get("OPUS_MODEL", "claude-opus-4-7")
sb = RSC.sb


def get_tenant_id(ident: str) -> str:
    t = sb().table("tenants").select("id,slug")
    rows = (t.eq("id", ident) if RSC._UUID.match(ident) else t.eq("slug", ident)).limit(1).execute().data
    if not rows:
        raise SystemExit(f"[tuning] no tenant matching {ident!r}")
    return rows[0]["id"]


def load_stats(tenant_id: str):
    rows = sb().table("attribute_stats").select("*").eq("tenant_id", tenant_id).execute().data or []
    return rows, {(r["context_key"], r["attribute_key"], r["dimension"]): r for r in rows}


def make_opus(tenant_id: str):
    client = Anthropic(api_key=RSC.get_anthropic_key(tenant_id))

    def call(system: str, user: str) -> str:
        r = client.messages.create(model=OPUS_MODEL, max_tokens=600, system=system,
                                    messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
    return call


def cmd_propose(tenant_id: str, top_k: int) -> None:
    rows, _ = load_stats(tenant_id)
    weak = T.find_weak_attributes(rows, top_k=top_k)
    if not weak:
        print("[tuning] no attributes meet the weakness + evidence bar yet — nothing to propose.")
        return
    print(f"[tuning] {len(weak)} weak attribute/dimension pairs; asking Opus…")
    opus = make_opus(tenant_id)
    cands = []
    for w in weak:
        c = T.propose_edit(opus, w)
        verdict = (c["suggested_edit"][:60] + "…") if c else "MODEL_LIMIT_NO_EDIT"
        print(f"  {w['scope_key']:28s} -> {w['dimension']:22s} (-{w['deficit']}): {verdict}")
        if c:
            cands.append(c)
    n = T.store_candidates(sb(), tenant_id, cands)
    print(f"[tuning] stored {n} candidate edit(s).")


def cmd_list(tenant_id: str) -> None:
    rows = sb().table("tuning_suggestions").select("*").eq("tenant_id", tenant_id).execute().data or []
    if not rows:
        print("[tuning] no suggestions."); return
    for st in ("candidate", "testing", "validated", "rejected"):
        sub = [r for r in rows if r.get("status") == st]
        if not sub:
            continue
        print(f"\n=== {st} ({len(sub)}) ===")
        for r in sub:
            d = f" Δ{r['score_delta']}" if r.get("score_delta") is not None else ""
            print(f"  [{r['id']}] {r['scope_key']} -> {r['dimension']}{d}\n      {r.get('suggested_edit','')}")


def cmd_promote(tenant_id: str, sid: str, promote_all: bool) -> None:
    _, stats = load_stats(tenant_id)
    if promote_all:
        cands = sb().table("tuning_suggestions").select("id").eq("tenant_id", tenant_id).eq("status", "candidate").execute().data or []
        for c in cands:
            T.promote_to_testing(sb(), tenant_id, c["id"], stats)
        print(f"[tuning] promoted {len(cands)} candidate(s) to testing.")
    elif sid:
        r = T.promote_to_testing(sb(), tenant_id, sid, stats)
        print(f"[tuning] {sid}: {r}")
    else:
        raise SystemExit("promote needs --id <suggestion_id> or --all")


def cmd_validate(tenant_id: str) -> None:
    _, stats = load_stats(tenant_id)
    res = T.ab_validate(sb(), tenant_id, stats)
    print(f"[tuning] A/B result -> validated {res['validated']}, rejected {res['rejected']}, waiting {res['waiting']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["propose", "list", "promote", "validate"])
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--id")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--top-k", type=int, default=15)
    args = ap.parse_args()
    tid = get_tenant_id(args.tenant)
    if args.command == "propose":
        cmd_propose(tid, args.top_k)
    elif args.command == "list":
        cmd_list(tid)
    elif args.command == "promote":
        cmd_promote(tid, args.id, args.all)
    elif args.command == "validate":
        cmd_validate(tid)


if __name__ == "__main__":
    main()
