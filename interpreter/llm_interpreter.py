"""
LLM interpreter layer.
Takes raw scraped rate records and uses Claude (Anthropic API) to:
  1. Classify rate type (NR, FLEX, BB, DBB, SPA, RO)
  2. Map to internal room type (STD, LUX, FAM_STD, FAM_LUX)
  3. Extract any included meals / cancellation policy
  4. Flag anomalies (price too low/high, duplicate, unavailable)
  5. Tier competitor vs target property
"""

import json
import logging
import os
from typing import Optional
import anthropic

logger = logging.getLogger(__name__)


MASTER_PROMPT = """
You are a hospitality revenue management expert interpreting OTA rate data for {target_name},
a hotel/serviced apartment in South Africa.

Your task: analyse the following list of raw scraped rate records and return a structured
JSON interpretation for each record.

=== RATE TYPE TAXONOMY ===
- NR  = Non-Refundable / Prepaid (no refund on cancellation)
- FLEX = Flexible / Free Cancellation (can cancel without penalty)
- BB  = Bed & Breakfast (breakfast included)
- DBB = Dinner Bed & Breakfast / Half Board (breakfast + dinner included)
- SPA = Spa Inclusive (spa credit or treatment package bundled)
- RO  = Room Only (no meals, no extras — including self-catering / serviced apartment)
- UNKNOWN = cannot be determined

=== ROOM TYPE TAXONOMY ===
For The Tyrwhitt specifically:
- STD       = Standard 1-bedroom apartment, max 2 guests
- LUX       = Luxury 1-bedroom apartment, max 2 guests
- FAM_STD   = Family Standard 2-bedroom apartment, max 4 guests
- FAM_LUX   = Family Luxury 2-bedroom apartment, max 4 guests
- UNKNOWN   = cannot be determined

For COMPETITOR properties, use these generic codes:
- SINGLE     = Single / Standard room (1 pax)
- STANDARD   = Standard double/twin (2 pax)
- SUPERIOR   = Superior / Deluxe room (2 pax)
- SUITE      = Suite / Apartment (2-4 pax)
- FAMILY     = Family room (3-4 pax)
- UNKNOWN    = cannot be determined

=== COMPETITOR TIERING ===
The TARGET property is: {target_name}
Mark is_target_property=true for any record whose property_name closely matches this name.

Based on price_zar and star rating (infer from name if possible), classify each rate as:
- MORE_EXPENSIVE   = competitor charges more than the target property's rate
- COMPARABLE       = within ±15% of the target property's rates
- CHEAPER          = competitor charges less than the target property
- TARGET           = this IS the target property record (not a competitor)

=== ANOMALY FLAGS ===
Flag any record with:
- "low_price"   : price < R300/night (likely error or different currency)
- "high_price"  : price > R15000/night (luxury outlier — still report, just flag)
- "no_price"    : price_zar is null
- "fallback"    : scraped via fallback method (less reliable)

=== YOUR OUTPUT FORMAT ===
Return ONLY a valid JSON array. No explanations, no markdown code fences.
Each element must have exactly these fields:

{
  "original_index": <int>,         // position in the input array (0-based)
  "channel": "<string>",
  "property_name": "<string>",
  "check_in": "<YYYY-MM-DD>",
  "check_out": "<YYYY-MM-DD>",
  "room_label_raw": "<string>",    // original label from OTA
  "rate_label_raw": "<string>",    // original rate plan label from OTA
  "price_zar": <float|null>,
  "rate_type": "<NR|FLEX|BB|DBB|SPA|RO|UNKNOWN>",
  "room_type": "<code from taxonomy above>",
  "meals_included": "<string>",    // e.g. "Breakfast", "Breakfast + Dinner", "None"
  "cancellation": "<string>",      // e.g. "Free cancellation", "Non-refundable", "Unknown"
  "is_target_property": <bool>,    // true if this is the target property record
  "competitor_tier": "<MORE_EXPENSIVE|COMPARABLE|CHEAPER|TARGET|UNKNOWN>",
  "anomaly_flags": ["<flag1>", ...],  // empty array if none
  "confidence": "<HIGH|MEDIUM|LOW>",  // how confident you are in the classification
  "notes": "<string>"              // any useful interpretation note, or empty string
}

=== RECORDS TO INTERPRET ===
{records_json}
"""


def _repair_json_array(text: str) -> list:
    """Parse a JSON array; if truncated/malformed, salvage every complete object before the break."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("[")
    if start == -1:
        raise json.JSONDecodeError("no JSON array found", text, 0)
    decoder = json.JSONDecoder()
    items, pos = [], start + 1
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            break
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            break  # truncated object — drop it
        items.append(obj)
    if not items:
        raise json.JSONDecodeError("no complete records recoverable", text, 0)
    logger.warning(f"[interpreter] repaired truncated JSON: recovered {len(items)} records")
    return items


class LLMInterpreter:
    MODEL = "claude-haiku-4-5-20251001"   # fast + cheap for batch interpretation
    MAX_RECORDS_PER_CALL = 12  # small batches keep the JSON response from truncating

    def __init__(self, api_key: Optional[str] = None, target_name: str = "the target property"):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.target_name = target_name

    def interpret(self, raw_records: list[dict]) -> list[dict]:
        """
        Send raw scraped records to Claude for classification.
        Batches in chunks to avoid token limits.
        Returns interpreted records in same order.
        """
        if not raw_records:
            return []

        all_results = []
        offset = 0

        for i in range(0, len(raw_records), self.MAX_RECORDS_PER_CALL):
            batch = raw_records[i:i + self.MAX_RECORDS_PER_CALL]
            # Re-index within batch
            for j, rec in enumerate(batch):
                rec["_batch_index"] = j

            logger.info(f"[interpreter] sending batch {i//self.MAX_RECORDS_PER_CALL + 1} "
                        f"({len(batch)} records) to LLM")

            prompt = MASTER_PROMPT.replace(
                "{target_name}", self.target_name
            ).replace(
                "{records_json}",
                json.dumps(batch, indent=2)
            )

            try:
                response = self.client.messages.create(
                    model=self.MODEL,
                    max_tokens=8192,
                    messages=[{"role": "user", "content": prompt}]
                )
                raw_text = response.content[0].text.strip()

                # Strip markdown fences if model wrapped in them
                if raw_text.startswith("```"):
                    raw_text = raw_text.split("```")[1]
                    if raw_text.startswith("json"):
                        raw_text = raw_text[4:]
                    raw_text = raw_text.strip()

                interpreted = _repair_json_array(raw_text)

                # Re-attach original data
                for item in interpreted:
                    orig_idx = item.get("original_index", 0)
                    if orig_idx < len(batch):
                        item["_raw"] = batch[orig_idx]
                    item["original_index"] = i + orig_idx  # global index

                all_results.extend(interpreted)

                # Any records lost to truncation get rule-based classification
                got = {item["original_index"] for item in interpreted}
                missing = [(j, rec) for j, rec in enumerate(batch) if i + j not in got]
                if missing:
                    from .rule_interpreter import rule_interpret
                    fallback = rule_interpret([rec for _, rec in missing], self.target_name)
                    for (j, _), item in zip(missing, fallback):
                        item["original_index"] = i + j
                        item["notes"] = "Rule-based (LLM output truncated)"
                        all_results.append(item)

            except json.JSONDecodeError as e:
                logger.error(f"[interpreter] JSON parse error in batch {i}: {e} — using rules for batch")
                from .rule_interpreter import rule_interpret
                for j, item in enumerate(rule_interpret(batch, self.target_name)):
                    item["original_index"] = i + j
                    item["notes"] = f"Rule-based (LLM parse error: {e})"
                    all_results.append(item)
            except Exception as e:
                logger.error(f"[interpreter] LLM call failed: {e}")
                for j, rec in enumerate(batch):
                    all_results.append({
                        "original_index": i + j,
                        "channel": rec.get("channel", ""),
                        "property_name": rec.get("property_name", ""),
                        "check_in": rec.get("check_in", ""),
                        "check_out": rec.get("check_out", ""),
                        "room_label_raw": rec.get("room_label", ""),
                        "rate_label_raw": rec.get("rate_label", ""),
                        "price_zar": rec.get("price_zar"),
                        "rate_type": "UNKNOWN",
                        "room_type": "UNKNOWN",
                        "meals_included": "Unknown",
                        "cancellation": "Unknown",
                        "is_target_property": False,
                        "competitor_tier": "UNKNOWN",
                        "anomaly_flags": ["llm_api_error"],
                        "confidence": "LOW",
                        "notes": str(e),
                        "_raw": rec
                    })

        logger.info(f"[interpreter] interpretation complete: {len(all_results)} records")
        return all_results
