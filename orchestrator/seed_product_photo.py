"""
orchestrator/seed_product_photo.py — one-time: put the tenant's product reference
photo into Supabase Storage + register it, so Step 2 (Qwen) can composite it.

Uploads <image> to the 'products' bucket at <tenant_id>/product.jpg, upserts a
media_assets row (kind=product_reference), and sets products.reference_asset_id.

RUN (locally, from orchestrator/, .venv active):
  python seed_product_photo.py                       # uses ../assets/product.jpg + default tenant
  python seed_product_photo.py --image C:\\path\\to\\product.jpg
  python seed_product_photo.py --tenant <tenant_uuid> --image <path>
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

DEFAULT_TENANT = "6f94981f-f27d-4dd8-836e-8c983f29116d"
DEFAULT_IMAGE = HERE.parent / "assets" / "product.jpg"   # repo_root/assets/product.jpg

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=DEFAULT_TENANT)
    ap.add_argument("--image", default=str(DEFAULT_IMAGE))
    args = ap.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        raise SystemExit(f"image not found: {img_path}")
    mime = _MIME.get(img_path.suffix.lower(), "image/jpeg")
    img = img_path.read_bytes()

    sb: Client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])

    # deterministic path so re-runs overwrite cleanly
    ext = img_path.suffix.lower() or ".jpg"
    path = f"{args.tenant}/product{ext}"
    sb.storage.from_("products").upload(path, img, {"content-type": mime, "upsert": "true"})
    print(f"[seed] uploaded {len(img)} bytes -> products/{path}")

    asset = sb.table("media_assets").upsert({
        "tenant_id": args.tenant, "kind": "product_reference", "bucket": "products",
        "path": path, "mime_type": mime, "bytes": len(img), "created_by_stage": "seed",
    }, on_conflict="bucket,path").execute().data[0]
    print(f"[seed] media_assets id = {asset['id']}")

    sb.table("products").update({"reference_asset_id": asset["id"]}).eq("tenant_id", args.tenant).execute()
    print(f"[seed] products.reference_asset_id set for tenant {args.tenant}")
    print("[seed] done — Step 2 can now find the product photo.")


if __name__ == "__main__":
    main()
