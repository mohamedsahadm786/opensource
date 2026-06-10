"""
orchestrator/seed_product_angle.py — one-time: register the OPTIONAL second
product reference photo (a 3/4 angled view of the SAME box) so Step 2 (Qwen)
can pass it as Picture 3 and understand the box's 3D depth.

Uploads <image> to the 'products' bucket at <tenant_id>/product_angle<ext>,
upserts a media_assets row (kind=product_reference_angle), and sets
products.reference_angle_asset_id. Requires migration 020.

The photo should be the SAME box as the main reference, plain background,
similar lighting, rotated ~30-45 degrees so the top and one side face show.

RUN (locally, from orchestrator/, venv active):
  python seed_product_angle.py --image C:\\path\\to\\product_angle.jpg
  python seed_product_angle.py --tenant <tenant_uuid> --image <path>
  python seed_product_angle.py --tenant <tenant_uuid> --remove     # unset (back to 2-image)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

DEFAULT_TENANT = "6c7bf137-aeae-4914-8242-c107254c1156"   # sahad-c6fd0d (test tenant)

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=DEFAULT_TENANT)
    ap.add_argument("--image")
    ap.add_argument("--remove", action="store_true", help="unset reference_angle_asset_id (revert to 2-image)")
    args = ap.parse_args()

    sb: Client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])

    if args.remove:
        sb.table("products").update({"reference_angle_asset_id": None}).eq("tenant_id", args.tenant).execute()
        print(f"[seed-angle] cleared products.reference_angle_asset_id for tenant {args.tenant}")
        return

    if not args.image:
        raise SystemExit("usage: python seed_product_angle.py --image <path> [--tenant <uuid>]")
    img_path = Path(args.image)
    if not img_path.exists():
        raise SystemExit(f"image not found: {img_path}")
    mime = _MIME.get(img_path.suffix.lower(), "image/jpeg")
    img = img_path.read_bytes()

    ext = img_path.suffix.lower() or ".jpg"
    path = f"{args.tenant}/product_angle{ext}"
    sb.storage.from_("products").upload(path, img, {"content-type": mime, "upsert": "true"})
    print(f"[seed-angle] uploaded {len(img)} bytes -> products/{path}")

    asset = sb.table("media_assets").upsert({
        "tenant_id": args.tenant, "kind": "product_reference_angle", "bucket": "products",
        "path": path, "mime_type": mime, "bytes": len(img), "created_by_stage": "seed",
    }, on_conflict="bucket,path").execute().data[0]
    print(f"[seed-angle] media_assets id = {asset['id']}")

    sb.table("products").update({"reference_angle_asset_id": asset["id"]}).eq("tenant_id", args.tenant).execute()
    print(f"[seed-angle] products.reference_angle_asset_id set for tenant {args.tenant}")
    print("[seed-angle] done — Step 2 will now send Picture 3 (after the pod worker update).")


if __name__ == "__main__":
    main()
