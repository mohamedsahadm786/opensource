#!/usr/bin/env python3
"""
parity_from_audit.py — replay a real Stage-1 run through the isolated service
and compare against the original output.

It reads an existing `*_request.json` audit (written by src/step_1_pulid.py),
sends the EXACT same prompt + persona portrait + params + seed to the service,
saves the result next to the original, and tells you whether it's a pixel match.

USAGE:
  cd /workspace/stage1-service
  source /workspace/ai-toolkit/venv/bin/activate
  python3 parity_from_audit.py \
      --audit /workspace/alluvi-pipeline/outputs/.../03_step1_persona_request.json \
      --url http://127.0.0.1:8192

Interpreting the result:
  - audit seed is a NUMBER  -> output should be PIXEL-IDENTICAL to the original
    (same PulidWrapper, same inputs, same seed). MAE ~0 = perfect parity.
  - audit seed is NULL      -> the original used a random seed, so this is a
    fresh generation of the SAME persona/scene. Eyeball identity + quality
    (can't pixel-match a random seed). See note printed at the end.
"""

import argparse
import base64
import json
import sys
from pathlib import Path

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True, help="path to *_request.json")
    ap.add_argument("--url", default="http://127.0.0.1:8192")
    ap.add_argument("--out", default=None, help="where to save (default: beside audit)")
    args = ap.parse_args()

    audit_path = Path(args.audit)
    if not audit_path.exists():
        print(f"audit not found: {audit_path}")
        return 2

    audit = json.loads(audit_path.read_text())
    a = audit.get("arguments", {})
    print(f"[parity] endpoint in audit : {audit.get('endpoint')}")
    print(f"[parity] persona portrait  : {a.get('persona_image_path')}")
    print(f"[parity] seed in audit     : {a.get('seed')}  "
          f"({'fixed -> expect pixel match' if a.get('seed') is not None else 'random -> qualitative compare'})")
    print(f"[parity] size/steps        : {a.get('width')}x{a.get('height')} / {a.get('num_inference_steps')} steps")
    print(f"[parity] id_weight/true_cfg: {a.get('id_weight')} / {a.get('true_cfg')}")

    persona_path = a.get("persona_image_path")
    if not persona_path or not Path(persona_path).exists():
        print(f"[parity] ERROR persona portrait missing on disk: {persona_path}")
        return 2

    body = {
        "step_1_prompt": a["prompt"],
        "persona_image_path": persona_path,
        "pulid_params": {
            "image_size": {"width": a.get("width", 768), "height": a.get("height", 1344)},
            "num_inference_steps": a.get("num_inference_steps", 30),
            "guidance_scale": a.get("guidance_scale", 3.5),
            "true_cfg": a.get("true_cfg", 1.5),
            "id_weight": a.get("id_weight", 0.6),
            "negative_prompt": a.get("negative_prompt"),
            "max_sequence_length": a.get("max_sequence_length", 512),
            "seed": a.get("seed"),
        },
        "scenario_id": "parity",
    }

    out_path = Path(args.out) if args.out else audit_path.parent / "03_step1_persona_SERVICE.jpg"
    print(f"[parity] calling service ... (first call loads PuLID, ~30-60s)")
    r = requests.post(f"{args.url}/stage1/generate", json=body, timeout=1800)
    if r.status_code != 200:
        print("[parity] ERROR", r.status_code, r.text[:500])
        return 1
    data = r.json()
    out_path.write_bytes(base64.b64decode(data["image_b64"]))
    print(f"[parity] service image -> {out_path}")
    print(f"[parity] used_seed={data.get('seed')} elapsed={data.get('elapsed_seconds'):.1f}s")

    # compare to the original 03_step1_persona.jpg if present
    original = audit_path.parent / audit_path.name.replace("_request.json", ".jpg")
    if original.exists() and a.get("seed") is not None:
        try:
            from PIL import Image
            import numpy as np
            o = np.asarray(Image.open(original).convert("RGB"), dtype=np.float32)
            s = np.asarray(Image.open(out_path).convert("RGB").resize(
                Image.open(original).size), dtype=np.float32)
            mae = float(np.abs(o - s).mean())
            print(f"\n[parity] original: {original.name}")
            print(f"[parity] mean abs pixel diff (0-255): {mae:.3f}")
            if mae < 1.0:
                print("[parity] ✅ PIXEL MATCH — service reproduces the current pipeline exactly.")
            elif mae < 8.0:
                print("[parity] ✅ near-identical (tiny diff from JPEG re-encode/precision).")
            else:
                print("[parity] ⚠️ images differ — investigate before integrating.")
        except Exception as e:
            print(f"[parity] (pixel compare skipped: {e})")
    else:
        print("\n[parity] NOTE: original seed was random (or no original found).")
        print("[parity] Open both side by side and confirm SAME person + scene + real skin quality:")
        print(f"         original: {original}")
        print(f"         service : {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
