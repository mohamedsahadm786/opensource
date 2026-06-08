"""
orchestrator/run_recompute.py — run the learning recompute (L1+L2) on demand or on a schedule.

Rebuilds attribute_stats (per-tenant) + attribute_priors (global) from ALL asset_ratings.
Read-only w.r.t. the content pipeline. Schedule every ~6h (cron / APScheduler / a worker).

RUN:
  python run_recompute.py
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

import engine as E

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
_sb: Client | None = None


def sb() -> Client:
    global _sb
    if _sb is None:
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    return _sb


def main() -> None:
    print("[recompute] rebuilding attribute_stats + attribute_priors from all ratings…")
    res = E.recompute(sb())
    print(f"[recompute] done ✓  ratings={res['ratings']}  "
          f"stat_rows={res['stat_rows']}  prior_rows={res['prior_rows']}")


if __name__ == "__main__":
    main()
