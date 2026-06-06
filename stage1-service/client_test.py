#!/usr/bin/env python3
"""
stage1-service/client_test.py — parity / smoke test for the isolated service.

Confirms the service produces a Stage-1 image (and optionally a Phase-A portrait)
so you can eyeball it against the current in-process output before integrating.

USAGE (on the pod, any python with `requests`):
  # Stage 1 — pass a persona reference face + a prompt:
  python3 client_test.py stage1 \
      --url http://127.0.0.1:8192 \
      --persona /workspace/alluvi-pipeline/outputs/personas/liam_foster/portrait.jpg \
      --prompt "$(cat /path/to/a_step_1_image_prompt.txt)" \
      --seed 12345 \
      --out /workspace/stage1-service/_test_stage1.jpg

  # Phase A — invent a portrait from a prompt:
  python3 client_test.py phasea \
      --url http://127.0.0.1:8192 \
      --prompt "a 27-year-old man, ... (portrait_prompt)" \
      --seed 777 \
      --out /workspace/stage1-service/_test_portrait.jpg

  # Free VRAM:
  python3 client_test.py free --url http://127.0.0.1:8192 --target all

Parity check: run Stage 1 here with the SAME seed + prompt + persona image that a
current in-process run used, then compare the two JPGs. Identical model + params
=> matching identity/quality.
"""

import argparse
import base64
import json
import sys

import requests


def _b64_file(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _save_b64(b64: str, out: str):
    with open(out, "wb") as f:
        f.write(base64.b64decode(b64))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["health", "stage1", "phasea", "free"])
    ap.add_argument("--url", default="http://127.0.0.1:8192")
    ap.add_argument("--persona", help="path to reference face (stage1)")
    ap.add_argument("--prompt", help="step_1_image_prompt (stage1) or portrait_prompt (phasea)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--guidance", type=float, default=3.5)
    ap.add_argument("--true-cfg", type=float, default=1.5)
    ap.add_argument("--id-weight", type=float, default=1.0)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=1344)
    ap.add_argument("--target", default="all", help="free target: phasea|stage1|all")
    ap.add_argument("--out", default="_test_out.jpg")
    args = ap.parse_args()

    if args.mode == "health":
        r = requests.get(f"{args.url}/health", timeout=30)
        print(json.dumps(r.json(), indent=2))
        return 0

    if args.mode == "free":
        r = requests.post(f"{args.url}/free", json={"target": args.target}, timeout=120)
        print(json.dumps(r.json(), indent=2))
        return 0

    if not args.prompt:
        print("--prompt is required for stage1/phasea")
        return 2

    if args.mode == "phasea":
        body = {
            "portrait_prompt": args.prompt,
            "width": args.width, "height": args.height,
            "num_inference_steps": args.steps,
            "guidance_scale": args.guidance,
            "seed": args.seed,
        }
        r = requests.post(f"{args.url}/phasea/portrait", json=body, timeout=900)
    else:  # stage1
        if not args.persona:
            print("--persona (reference face path) is required for stage1")
            return 2
        body = {
            "step_1_prompt": args.prompt,
            "persona_image_b64": _b64_file(args.persona),
            "pulid_params": {
                "image_size": {"width": args.width, "height": args.height},
                "num_inference_steps": args.steps,
                "guidance_scale": args.guidance,
                "true_cfg": args.true_cfg,
                "id_weight": args.id_weight,
                "max_sequence_length": "512",
                "seed": args.seed,
            },
            "scenario_id": "paritytest",
        }
        r = requests.post(f"{args.url}/stage1/generate", json=body, timeout=900)

    if r.status_code != 200:
        print("ERROR", r.status_code, r.text[:500])
        return 1
    data = r.json()
    _save_b64(data["image_b64"], args.out)
    meta = {k: v for k, v in data.items() if k != "image_b64"}
    print(json.dumps(meta, indent=2))
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
