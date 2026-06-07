"""
orchestrator/generate_qc_brief.py — ONE-TIME per tenant (one product per tenant).

A Claude VISION pass over the real product photo + packaging data + mask_prompt
produces a precise QC ground-truth brief, stored in products.qc_brief. The QC
reviewer then injects this brief so product judgments are product-accurate.

In production this runs once at tenant signup (right after the product photo is
registered). Here it's a standalone utility.

RUN:
  python generate_qc_brief.py                       # default tenant, skip if brief exists
  python generate_qc_brief.py --force               # regenerate even if one exists
  python generate_qc_brief.py --show                # just print the stored brief
  python generate_qc_brief.py --tenant <uuid> [...]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from supabase import create_client, Client

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

BRIEF_MODEL = os.environ.get("QC_BRIEF_MODEL", os.environ.get("OPUS_MODEL", "claude-opus-4-7"))
RULES_DIR = Path(os.environ.get("RULES_DIR", HERE / "rules"))
DEFAULT_TENANT = "6f94981f-f27d-4dd8-836e-8c983f29116d"


def sb() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=DEFAULT_TENANT)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    db = sb()

    rows = db.table("products").select(
        "name,packaging,mask_prompt,reference_asset_id,qc_brief").eq("tenant_id", args.tenant).limit(1).execute().data
    if not rows:
        raise SystemExit("[qc-brief] no product for this tenant")
    product = rows[0]

    if args.show:
        print(product.get("qc_brief") or "(no qc_brief stored yet)")
        return
    if product.get("qc_brief") and not args.force:
        print("[qc-brief] a brief already exists — use --force to regenerate, --show to view.")
        return
    if not product.get("reference_asset_id"):
        raise SystemExit("[qc-brief] product has no reference photo (reference_asset_id null) — register it first")

    asset = db.table("media_assets").select("bucket,path,mime_type").eq(
        "id", product["reference_asset_id"]).limit(1).execute().data[0]
    image_bytes = db.storage.from_(asset["bucket"]).download(asset["path"])
    media_type = asset.get("mime_type") or "image/jpeg"

    api_key = db.rpc("get_tenant_anthropic_key", {"p_tenant_id": args.tenant}).execute().data
    if not api_key:
        raise SystemExit("[qc-brief] no Anthropic key in Vault for this tenant")

    details = {
        "name": product.get("name"),
        "packaging": product.get("packaging"),
        "mask_prompt": product.get("mask_prompt"),
    }
    user_text = (
        "Here is the product. Write the QC product brief per your instructions.\n\n"
        "Structured packaging data and mask prompt:\n"
        + json.dumps(details, indent=2, ensure_ascii=False))

    print(f"[qc-brief] generating via {BRIEF_MODEL} (vision over the product photo)…")
    resp = Anthropic(api_key=api_key).messages.create(
        model=BRIEF_MODEL, max_tokens=1200,
        system=(RULES_DIR / "qc_brief_builder.md").read_text(encoding="utf-8"),
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.b64encode(image_bytes).decode()}},
            {"type": "text", "text": user_text},
        ]}])
    brief = resp.content[0].text.strip()

    db.table("products").update({
        "qc_brief": brief,
        "qc_brief_generated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("tenant_id", args.tenant).execute()

    print("\n" + "=" * 60 + "\nQC PRODUCT BRIEF (stored in products.qc_brief)\n" + "=" * 60)
    print(brief)
    print("=" * 60)
    print(f"[qc-brief] stored ✓ ({len(brief)} chars). The QC reviewer will now use this as product ground-truth.")


if __name__ == "__main__":
    main()
