"""
orchestrator/backfill_attributes.py — compute composed_attributes for every scenario.

One-time (re-runnable) job: read each scenario's spec, run compose_attributes() (our vocab),
write it to scenarios.composed_attributes. Safe to re-run after extending the canon tables in
attributes.py — it simply recomputes. Scenarios are universal (no tenant), service key writes them.

RUN:
  python backfill_attributes.py            # backfill all
  python backfill_attributes.py --dry-run  # compute + print a sample, write nothing
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

import attributes as A

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
_sb: Client | None = None


def sb() -> Client:
    global _sb
    if _sb is None:
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    return _sb


def main() -> None:
    dry = "--dry-run" in sys.argv
    rows = sb().table("scenarios").select("id,scenario_key,spec").execute().data or []
    print(f"[backfill] {len(rows)} scenarios")
    updated = 0
    for i, r in enumerate(rows):
        comp = A.compose_attributes(r.get("spec") or {})
        if dry:
            if i < 3:
                print(f"  {r.get('scenario_key')}: {json.dumps(comp)}")
            continue
        sb().table("scenarios").update({"composed_attributes": comp}).eq("id", r["id"]).execute()
        updated += 1
    if dry:
        print("[backfill] --dry-run: nothing written.")
    else:
        print(f"[backfill] updated composed_attributes on {updated} scenarios ✓")


if __name__ == "__main__":
    main()
