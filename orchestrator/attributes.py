"""
orchestrator/attributes.py — canonicalize a scenario `spec` into our fixed attribute vocab.

The learning engine credits "ingredients" (attributes), not whole scenarios. This module is
the single source of truth for turning a (messy, free-text) spec into a small fixed-vocab dict.
Used in three places (must be identical everywhere): curated backfill, generated-scenario
creation, and the immutable snapshot copied into asset_ratings at rating time.

Direct fields come straight from the spec; free-text fields are matched against ordered
keyword tables (extend the tables as new phrasings appear). Everything is deterministic — no LLM.

  compose_attributes(spec: dict) -> dict   # the ~16 canonical keys
"""
from __future__ import annotations

from typing import Optional

# ── ordered keyword tables: first rule whose any-substring is found wins ────────
# (substrings, canonical)
_LIGHTING = [
    (("golden hour", "golden-hour", "sunset", "late-afternoon", "late afternoon"), "golden_hour"),
    (("warm and cool", "warm/cool", "warm-cool", "cool overhead", "dual", "mixed"), "dual_warm_cool"),
    (("overcast", "cloudy", "diffused grey", "diffused gray"), "overcast"),
    (("studio", "softbox", "key light"), "studio"),
    (("neon", "led", "colored light", "coloured light"), "neon"),
    (("candle", "lamp", "warm indoor", "warm interior", "warm tungsten", "cozy warm"), "warm_indoor"),
    (("cool indoor", "fluorescent", "blue hour"), "cool_indoor"),
    (("evening", "dusk", "night", "moon"), "evening"),
    (("bright", "midday", "harsh sun", "direct sun"), "bright_daylight"),
    (("soft", "natural", "window", "daylight", "morning"), "soft_daylight"),
]
_TIME = [
    (("golden hour", "golden-hour", "sunset", "late-afternoon", "late afternoon"), "golden_hour"),
    (("morning", "sunrise", "dawn"), "morning"),
    (("midday", "noon"), "midday"),
    (("afternoon",), "afternoon"),
    (("evening", "dusk"), "evening"),
    (("night", "moon", "nightlife"), "night"),
]
_FRAMING = [
    (("medium full", "mid-thigh", "three-quarter length", "knee up", "3/4 length"), "medium_full"),
    (("medium close", "medium-close", "chest up", "bust"), "medium_closeup"),
    (("close-up", "closeup", "close up", "face", "tight"), "closeup"),
    (("wide", "full body", "full-length", "environmental"), "wide"),
    (("medium",), "medium"),
]
_CAMHEIGHT = [
    (("overhead", "top-down", "top down", "bird"), "overhead"),
    (("low angle", "low-angle", "ground", "below"), "low"),
    (("waist",), "waist"),
    (("chest",), "chest_level"),
    (("eye", "head", "face level"), "eye_level"),
    (("high angle", "high-angle", "above"), "high"),
]
_MOOD = [
    (("calm", "composed", "serene", "tranquil", "still", "quiet"), "calm"),
    (("confident", "empowered", "self-assured", "poised"), "confident"),
    (("energetic", "winded", "dynamic", "lively", "upbeat", "post-workout"), "energetic"),
    (("focused", "determined", "intent", "concentrated"), "focused"),
    (("playful", "fun", "cheeky", "flirty"), "playful"),
    (("cozy", "warm", "comfort", "snug", "intimate"), "cozy"),
    (("aspirational", "luxury", "elevated", "premium"), "aspirational"),
    (("relaxed", "easy", "casual", "laid-back", "lounging"), "relaxed"),
    (("joy", "happy", "bright", "radiant"), "joyful"),
]
_PALETTE = [
    (("pastel", "muted soft", "powder"), "pastel"),
    (("earthy", "terracotta", "wood", "olive", "sand", "tan", "beige", "clay"), "earthy"),
    (("monochrome", "black and white", "greyscale", "grayscale"), "monochrome"),
    (("vibrant", "bold", "saturated", "colorful", "colourful"), "vibrant"),
    (("warm", "golden", "amber", "honey", "peach", "rust"), "warm"),
    (("cool", "blue", "teal", "slate", "steel", "icy"), "cool"),
    (("neutral", "bone", "concrete", "grey", "gray", "white", "charcoal", "stone", "cream"), "neutral"),
]
_POSE = [
    (("standing", "stands", "upright"), "standing"),
    (("seated", "sitting", "sits", "perched", "bench"), "seated"),
    (("leaning", "leans", "propped"), "leaning"),
    (("walking", "walks", "stepping", "mid-stride"), "walking"),
    (("crouch", "squat"), "crouching"),
    (("kneel",), "kneeling"),
    (("reclin", "lying", "lounging", "lay"), "reclining"),
]
_FREEHAND = [
    (("phone", "selfie", "filming", "capturing", "mirror reflection"), "hold_phone"),
    (("touch", "adjust the product", "on the product", "presenting"), "touch_product"),
    (("hair", "face", "neck", "chin"), "adjust_self"),
    (("gesture", "point", "wave", "open palm"), "gesture"),
    (("counter", "table", "surface", "shelf", "rest"), "rest_on_surface"),
    (("idle", "relaxed at side", "by the side", "still"), "idle"),
]
_OUTFIT = [
    (("athleisure", "athletic", "training", "sports bra", "leggings", "activewear", "gym", "workout"), "activewear"),
    (("loungewear", "robe", "pajama", "pyjama", "sleep", "knit set", "lounge"), "loungewear"),
    (("swim", "bikini", "swimsuit", "poolside"), "swimwear"),
    (("smart casual", "smart-casual", "blazer", "tailored", "shirt and"), "smart_casual"),
    (("formal", "suit", "gown", "dress shirt", "evening dress"), "formal"),
    (("outerwear", "coat", "jacket", "puffer", "parka", "trench"), "outerwear"),
    (("casual", "jeans", "tee", "t-shirt", "denim", "hoodie", "everyday"), "casual"),
]
_INDOOR_CATS = {"gym", "home_gym", "cafe", "office", "library", "kitchen", "bedroom", "bathroom",
                "closet", "studio", "spa", "sunroom", "greenhouse", "coworking", "pottery", "dance",
                "pilates", "yoga", "recovery", "music", "art", "boxing"}
_OUTDOOR_CATS = {"beach", "rooftop", "balcony", "garden", "poolside", "marina", "lakeside", "desert",
                 "nature", "outdoor", "street", "vineyard", "travel", "autumn", "winter", "cabin"}


def _match(text: Optional[str], table, default: str) -> str:
    if not text:
        return default
    low = str(text).lower()
    for subs, canon in table:
        if any(s in low for s in subs):
            return canon
    return default


def _slug(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    return str(v).strip().lower().replace(" ", "_").replace("/", "_") or None


def derive_grip(archetype: Optional[str], grip_text: Optional[str]) -> str:
    """grip_type from the archetype tag (already canonical), with a text fallback."""
    a = _slug(archetype)
    known = {"held_with_phone", "placed_on_surface", "held_product_high", "two_handed_cradle",
             "single_hand_pinch", "fingertips", "palm", "held_low", "held_product"}
    if a in known:
        return a
    low = (grip_text or "").lower()
    if "placed" in low or "on the surface" in low or "on a surface" in low or "resting on" in low:
        return "placed_on_surface"
    if "both hands" in low or "two hand" in low or "cradle" in low:
        return "two_handed_cradle"
    if "phone" in low:
        return "held_with_phone"
    if "pinch" in low or "thumb and" in low or "fingertip" in low:
        return "single_hand_pinch"
    return a or "held_product"


def _environment(category: Optional[str], scene_text: Optional[str]) -> str:
    c = _slug(category)
    if c in _INDOOR_CATS:
        return "indoor"
    if c in _OUTDOOR_CATS:
        return "outdoor"
    low = (scene_text or "").lower()
    if any(w in low for w in ("outdoor", "outside", "sky", "beach", "garden", "rooftop", "street", "park")):
        return "outdoor"
    return "indoor"


def compose_attributes(spec: dict) -> dict:
    spec = spec or {}
    ha = spec.get("hand_assignment") or {}
    outfit = spec.get("outfit") or {}
    phone_hand = _slug(ha.get("phone_hand")) or "none"
    return {
        "scene_category":   _slug(spec.get("category")),
        "environment":      _environment(spec.get("category"), spec.get("scene")),
        "lighting":         _match(spec.get("lighting"), _LIGHTING, "soft_daylight"),
        "time_of_day":      _match(spec.get("lighting"), _TIME, "indoor"),
        "framing":          _match(spec.get("framing"), _FRAMING, "medium"),
        "camera_height":    _match(spec.get("camera_height"), _CAMHEIGHT, "eye_level"),
        "mood":             _match(spec.get("mood"), _MOOD, "calm"),
        "palette":          _match(spec.get("palette"), _PALETTE, "neutral"),
        "pose":             _match(spec.get("pose"), _POSE, "standing"),
        "grip_type":        derive_grip(spec.get("archetype"), spec.get("grip_or_placement")),
        "product_hand":     _slug(ha.get("product_hand")),
        "phone_hand":       phone_hand,
        "has_phone":        phone_hand != "none",
        "free_hand_action": _match(ha.get("free_hand_action"), _FREEHAND, "idle"),
        "outfit_style":     _match(outfit.get("style_brief"), _OUTFIT, "casual"),
        "difficulty":       _slug(spec.get("difficulty")),
    }


VOCAB_KEYS = ["scene_category", "environment", "lighting", "time_of_day", "framing", "camera_height",
              "mood", "palette", "pose", "grip_type", "product_hand", "phone_hand", "has_phone",
              "free_hand_action", "outfit_style", "difficulty"]
