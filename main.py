#!/usr/bin/env python3
"""
Rate Intelligence Agent — Main Orchestrator
The Tyrwhitt, Rosebank

Usage:
    python main.py                        # full run (target + competitors, all channels)
    python main.py --channels booking_com # single channel
    python main.py --target-only          # skip competitor scraping
    python main.py --dry-run              # use cached/mock data (no browser)

Environment:
    ANTHROPIC_API_KEY   required for LLM interpretation
    RATE_TOOL_DEBUG     set to 1 for verbose logging
"""

import asyncio
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from scrapers import BookingScraper, ExpediaScraper, AgodaScraper, LekkeSlaapScraper, SAVenuesScraper
from interpreter import LLMInterpreter
from interpreter.rule_interpreter import rule_interpret, clean_property_name, is_target_property
from reports import generate_report

# ── Config ───────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(os.environ.get("RATE_TOOL_CONFIG", str(Path(__file__).parent / "config" / "properties.json")))

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("RATE_TOOL_DEBUG") else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("rate_tool")

SCRAPER_MAP = {
    "booking_com":  BookingScraper,
    "expedia":      ExpediaScraper,
    "agoda":        AgodaScraper,
    "lekkeslaap":   LekkeSlaapScraper,
    "sa_venues":    SAVenuesScraper,
}

ALL_CHANNELS = list(SCRAPER_MAP.keys())


# ── Scrape layer ──────────────────────────────────────────────────────────────

async def scrape_property(scraper_cls, config: dict, search_term: str) -> list[dict]:
    scraper = scraper_cls(config)
    return await scraper.run(search_term)


async def scrape_all_channels(config: dict, search_term: str, channels: list[str]) -> list[dict]:
    """Run all channel scrapers concurrently for a single property."""
    tasks = [
        scrape_property(SCRAPER_MAP[ch], config, search_term)
        for ch in channels
        if ch in SCRAPER_MAP
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    combined = []
    for ch, res in zip(channels, results):
        if isinstance(res, Exception):
            logger.error(f"Scraper [{ch}] raised exception: {res}")
        else:
            combined.extend(res)
    return combined


async def run_scrape(config: dict, channels: list[str], target_only: bool) -> list[dict]:
    target_cfg = config["target_property"]
    target_search = target_cfg["booking_com_search"]  # general enough for all channels

    logger.info(f"── Scraping target: {target_cfg['name']} ──")
    raw = await scrape_all_channels(config, target_search, channels)
    logger.info(f"Target scrape complete: {len(raw)} raw records")

    if not target_only:
        comp_set = config["competitor_set"]["node_comps"]
        logger.info(f"── Scraping {len(comp_set)} competitors ──")
        for comp_name in comp_set:
            logger.info(f"Scraping competitor: {comp_name}")
            comp_raw = await scrape_all_channels(config, comp_name, channels)
            raw.extend(comp_raw)
            logger.info(f"  → {len(comp_raw)} records for {comp_name}")

    logger.info(f"Total raw records: {len(raw)}")
    return raw


def postprocess(interpreted: list[dict], target_name: str) -> list[dict]:
    """Clean names, re-detect target records, then tier competitors vs the target avg rate."""
    for r in interpreted:
        r["property_name"] = clean_property_name(r.get("property_name", ""))
        if is_target_property(r["property_name"], target_name):
            r["is_target_property"] = True
        if r.get("is_target_property"):
            r["competitor_tier"] = "TARGET"

    target_prices = [r["price_zar"] for r in interpreted
                     if r.get("is_target_property") and r.get("price_zar")]
    if not target_prices:
        logger.warning("No target property rates found — competitor tiers will be UNKNOWN")
        return interpreted
    target_avg = sum(target_prices) / len(target_prices)
    logger.info(f"Target avg rate: R{target_avg:,.0f} ({len(target_prices)} records)")

    for r in interpreted:
        if r.get("is_target_property"):
            continue
        price = r.get("price_zar")
        if not price:
            r["competitor_tier"] = "UNKNOWN"
        elif price > target_avg * 1.15:
            r["competitor_tier"] = "MORE_EXPENSIVE"
        elif price < target_avg * 0.85:
            r["competitor_tier"] = "CHEAPER"
        else:
            r["competitor_tier"] = "COMPARABLE"
    return interpreted


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rate Intelligence Agent")
    parser.add_argument("--channels", nargs="+", choices=ALL_CHANNELS, default=ALL_CHANNELS,
                        help="OTA channels to scrape (default: all)")
    parser.add_argument("--target-only", action="store_true",
                        help="Skip competitor scraping")
    parser.add_argument("--dry-run", action="store_true",
                        help="Load cached raw data instead of scraping (for testing)")
    parser.add_argument("--no-interpret", action="store_true",
                        help="Skip LLM interpretation (saves API cost, raw data only)")
    parser.add_argument("--output-dir", default="output",
                        help="Output directory for reports (default: ./output)")
    args = parser.parse_args()

    # Load config
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    target_name = clean_property_name(config["target_property"]["name"])
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_json_path = output_dir / f"raw_rates_{run_ts}.json"
    interpreted_json_path = output_dir / f"interpreted_rates_{run_ts}.json"
    excel_path = output_dir / f"Rate_Intelligence_{target_name.replace(' ', '_')}_{run_ts}.xlsx"

    # ── Step 1: Scrape ──
    if args.dry_run:
        # Load most recent raw JSON if available
        raw_files = sorted(output_dir.glob("raw_rates_*.json"), reverse=True)
        if raw_files:
            logger.info(f"[dry-run] loading cached raw data: {raw_files[0]}")
            with open(raw_files[0]) as f:
                raw_records = json.load(f)
        else:
            logger.warning("[dry-run] no cached data found, running real scrape")
            raw_records = asyncio.run(run_scrape(config, args.channels, args.target_only))
    else:
        raw_records = asyncio.run(run_scrape(config, args.channels, args.target_only))

    # Save raw
    with open(raw_json_path, "w") as f:
        json.dump(raw_records, f, indent=2)
    logger.info(f"Raw records saved: {raw_json_path}")

    if not raw_records:
        logger.error("No data scraped. Check your internet connection and OTA selectors.")
        sys.exit(1)

    # ── Step 2: Interpret ──
    if args.no_interpret:
        logger.info("[--no-interpret] skipping LLM interpretation")
        interpreted = []
        for i, r in enumerate(raw_records):
            interpreted.append({
                "original_index": i,
                "channel": r.get("channel", ""),
                "property_name": r.get("property_name", ""),
                "check_in": r.get("check_in", ""),
                "check_out": r.get("check_out", ""),
                "room_label_raw": r.get("room_label", ""),
                "rate_label_raw": r.get("rate_label", ""),
                "price_zar": r.get("price_zar"),
                "rate_type": "UNKNOWN",
                "room_type": "UNKNOWN",
                "meals_included": "Unknown",
                "cancellation": "Unknown",
                "is_target_property": target_name.lower() in r.get("property_name", "").lower(),
                "competitor_tier": "UNKNOWN",
                "anomaly_flags": [],
                "confidence": "LOW",
                "notes": "LLM interpretation skipped",
                "_raw": r
            })
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not set — using rule-based classification")
            interpreted = rule_interpret(raw_records, target_name=target_name)
        else:
            interpreter = LLMInterpreter(api_key=api_key, target_name=target_name)
            logger.info(f"── Interpreting {len(raw_records)} records via LLM ──")
            try:
                interpreted = interpreter.interpret(raw_records)
            except Exception as e:
                logger.error(f"LLM interpretation failed ({e}) — falling back to rule-based")
                interpreted = rule_interpret(raw_records, target_name=target_name)

    interpreted = postprocess(interpreted, target_name)

    # Save interpreted
    with open(interpreted_json_path, "w") as f:
        json.dump(interpreted, f, indent=2, default=str)
    logger.info(f"Interpreted records saved: {interpreted_json_path}")

    # ── Step 3: Generate report ──
    logger.info(f"── Generating Excel report ──")
    out = generate_report(interpreted, str(excel_path), target_name=target_name)
    logger.info(f"✓ Report saved: {out}")

    print(f"\n{'='*60}")
    print(f"  RATE INTELLIGENCE REPORT COMPLETE")
    print(f"  Property : {target_name}")
    print(f"  Channels : {', '.join(args.channels)}")
    print(f"  Records  : {len(raw_records)} scraped → {len(interpreted)} interpreted")
    print(f"  Report   : {excel_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
