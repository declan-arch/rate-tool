"""
Rule-based interpreter — classifies rates WITHOUT needing the Anthropic API.
Used as a fallback when LLM is unavailable, or as a fast pre-pass.

Handles:
  - rate_type classification (NR, FLEX, BB, DBB, SPA, RO)
  - room_type classification (generic: SINGLE, STANDARD, SUPERIOR, SUITE, FAMILY)
  - is_target_property detection (fuzzy name match)
  - competitor_tier assignment
  - anomaly flags
"""

import re
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# ── Keyword patterns ──────────────────────────────────────────────────────────

RATE_TYPE_RULES = [
    # Order matters — check most specific first
    ("DBB",  [r"dinner.*bed.*breakfast", r"half.?board", r"dbb", r"bed.*breakfast.*dinner", r"dinner.*b&b"]),
    ("BB",   [r"\bb&b\b", r"bed.*breakfast", r"breakfast.*incl", r"with breakfast", r"includes breakfast"]),
    ("SPA",  [r"spa", r"wellness", r"treatment", r"massage"]),
    ("NR",   [r"non.?refund", r"prepaid", r"no cancel", r"cannot be cancelled", r"advance purchase",
              r"pay now", r"non refundable", r"non-refundable"]),
    ("FLEX", [r"free cancel", r"fully refund", r"flexible", r"can be cancelled", r"refundable",
              r"no fee", r"cancel free"]),
    ("RO",   [r"room only", r"self.?cater", r"no meal", r"apartment", r"studio", r"serviced"]),
]

ROOM_TYPE_RULES = [
    # suites / apartments first (more specific)
    ("SUITE",    [r"suite", r"penthouse", r"presidential", r"executive suite", r"junior suite",
                  r"apartment", r"flat", r"studio"]),
    ("FAMILY",   [r"family", r"2.?bed", r"two.?bed", r"triple", r"quad", r"connecting"]),
    ("SUPERIOR", [r"deluxe", r"superior", r"premium", r"luxury", r"king", r"junior"]),
    ("STANDARD", [r"standard", r"classic", r"twin", r"double", r"queen", r"budget"]),
    ("SINGLE",   [r"single", r"solo", r"1 pax", r"one person"]),
]

# Tyrwhitt-specific overrides (when target property)
TYRWHITT_ROOM_RULES = [
    ("FAM_LUX",  [r"family.*lux", r"lux.*family", r"2.?bed.*lux", r"lux.*2.?bed"]),
    ("FAM_STD",  [r"family", r"2.?bed", r"two.?bed"]),
    ("LUX",      [r"luxury", r"lux\b", r"deluxe", r"superior", r"premium"]),
    ("STD",      [r"standard", r"classic", r"twin", r"double", r"studio", r"1.?bed"]),
]


def _match(text: str, patterns: list[str]) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in patterns)


def classify_rate_type(room_label: str, rate_label: str) -> str:
    combined = f"{room_label} {rate_label}".lower()
    for code, patterns in RATE_TYPE_RULES:
        if _match(combined, patterns):
            return code
    return "RO"  # default: room only (most common OTA listing)


def classify_room_type(room_label: str, rate_label: str, is_target: bool = False) -> str:
    combined = f"{room_label} {rate_label}".lower()
    rules = TYRWHITT_ROOM_RULES if is_target else ROOM_TYPE_RULES
    for code, patterns in rules:
        if _match(combined, patterns):
            return code
    return "STANDARD"  # sensible default


def infer_meals(rate_type: str, combined: str) -> str:
    if rate_type == "BB":
        return "Breakfast"
    if rate_type == "DBB":
        return "Breakfast + Dinner"
    if _match(combined, [r"lunch"]):
        return "Lunch"
    return "None"


def infer_cancellation(rate_type: str, combined: str) -> str:
    if rate_type == "NR":
        return "Non-refundable"
    if rate_type == "FLEX":
        return "Free cancellation"
    if _match(combined, [r"non.?refund", r"no refund", r"prepaid"]):
        return "Non-refundable"
    if _match(combined, [r"free cancel", r"can cancel", r"refundable"]):
        return "Free cancellation"
    return "See property policy"


def clean_property_name(name: str) -> str:
    """
    Repair names mangled by concatenation, e.g.
    "The Royal MajRoyal Majestic Hotel, Rosebank Johannesburg"
      -> "The Royal Majestic Hotel, Rosebank Johannesburg"
    Handles (1) a whole first half repeated and (2) a fragment whose tail
    reappears immediately after it (overlap).
    """
    if not name:
        return name
    s = re.sub(r"\s+", " ", name).strip()

    # (1) "X X" -> "X" (optionally with a separator between the halves)
    m = re.fullmatch(r"(.{6,}?)[\s,\-|]*\1", s, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # (2) fragment overlap: head ends with the same text that starts the remainder
    min_overlap = 5
    for i in range(min_overlap, len(s) - min_overlap):
        head, rest = s[:i], s[i:]
        for k in range(min(len(head), len(rest)), min_overlap - 1, -1):
            if head[-k:].lower() == rest[:k].lower():
                return (head[:-k] + rest).strip()
    return s


def _core(text: str) -> str:
    text = clean_property_name(text).lower()
    for n in [r"\bhotel\b", r"\bthe\b", r"\brosebank\b", r"\bsandton\b", r"\bmidrand\b",
              r"\bjohannesburg\b", r"\bsa\b", r"\bsouth africa\b"]:
        text = re.sub(n, "", text)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())


def fuzzy_match(a: str, b: str, threshold: float = 0.55) -> bool:
    """True if strings are similar enough to be the same property."""
    a, b = clean_property_name(a).lower().strip(), clean_property_name(b).lower().strip()
    ca, cb = _core(a), _core(b)
    if ca and cb and (ca in cb or cb in ca):
        return True
    if a == b:
        return True
    # Remove common noise words
    noise = [r"\bhotel\b", r"\bthe\b", r"\brosebank\b", r"\bsandton\b",
             r"\bjohannesburg\b", r"\bsa\b", r"\bsouth africa\b"]
    for n in noise:
        a = re.sub(n, "", a).strip()
        b = re.sub(n, "", b).strip()
    if a == b:
        return True
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold


def is_target_property(property_name: str, target_name: str) -> bool:
    return fuzzy_match(property_name, target_name)


def classify_competitor_tier(price_zar, target_avg_rate: float = None,
                              is_target: bool = False) -> str:
    if is_target:
        return "TARGET"
    if price_zar is None:
        return "UNKNOWN"
    # If we don't know the target rate, use rough Rosebank 4-star baseline ~R2000/night
    ref = target_avg_rate or 2000.0
    ratio = price_zar / ref
    if ratio > 1.15:
        return "MORE_EXPENSIVE"
    elif ratio < 0.85:
        return "CHEAPER"
    else:
        return "COMPARABLE"


def flag_anomalies(price_zar, extras: dict) -> list[str]:
    flags = []
    if price_zar is None:
        flags.append("no_price")
    elif price_zar < 300:
        flags.append("low_price")
    elif price_zar > 15000:
        flags.append("high_price")
    if extras and extras.get("fallback"):
        flags.append("fallback")
    return flags


# ── Public entry point ────────────────────────────────────────────────────────

def rule_interpret(raw_records: list[dict], target_name: str) -> list[dict]:
    """
    Apply rule-based classification to raw scraped records.
    Returns list of interpreted dicts matching the LLM interpreter's output schema.
    """
    logger.info(f"[rule-interpreter] classifying {len(raw_records)} records (no LLM needed)")

    # First pass: figure out which records are the target property
    interpreted = []
    for i, r in enumerate(raw_records):
        prop = clean_property_name(r.get("property_name", ""))
        is_target = is_target_property(prop, target_name)

        room_label = r.get("room_label", "")
        rate_label = r.get("rate_label", "")
        combined = f"{room_label} {rate_label}"
        price_zar = r.get("price_zar")
        extras = r.get("extras", {})

        rate_type = classify_rate_type(room_label, rate_label)
        room_type = classify_room_type(room_label, rate_label, is_target=is_target)
        meals = infer_meals(rate_type, combined)
        cancellation = infer_cancellation(rate_type, combined)
        anomalies = flag_anomalies(price_zar, extras)
        tier = classify_competitor_tier(price_zar, is_target=is_target)

        interpreted.append({
            "original_index": i,
            "channel": r.get("channel", ""),
            "property_name": prop,
            "check_in": r.get("check_in", ""),
            "check_out": r.get("check_out", ""),
            "room_label_raw": room_label,
            "rate_label_raw": rate_label,
            "price_zar": price_zar,
            "rate_type": rate_type,
            "room_type": room_type,
            "meals_included": meals,
            "cancellation": cancellation,
            "is_target_property": is_target,
            "competitor_tier": tier,
            "anomaly_flags": anomalies,
            "confidence": "MEDIUM",
            "notes": "Rule-based classification (no LLM)",
            "_raw": r,
        })

    # Second pass: recalculate competitor tier now we know target avg
    target_prices = [r["price_zar"] for r in interpreted
                     if r["is_target_property"] and r.get("price_zar")]
    target_avg = sum(target_prices) / len(target_prices) if target_prices else None

    if target_avg:
        for r in interpreted:
            if not r["is_target_property"]:
                r["competitor_tier"] = classify_competitor_tier(
                    r.get("price_zar"), target_avg_rate=target_avg, is_target=False
                )

    target_count = sum(1 for r in interpreted if r["is_target_property"])
    comp_count = len(interpreted) - target_count
    logger.info(f"[rule-interpreter] done: {target_count} target records, {comp_count} competitor records")
    return interpreted
