"""
Base scraper class — all channel scrapers inherit from this.
Handles browser launch, retry logic, and standard output schema.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


@dataclass
class RawRate:
    """Normalised raw rate record before LLM interpretation."""
    channel: str
    property_name: str
    check_in: str          # YYYY-MM-DD
    check_out: str         # YYYY-MM-DD
    room_label: str        # raw label from OTA
    rate_label: str        # raw rate/plan label from OTA
    price_zar: Optional[float]
    price_raw: str         # original string (e.g. "R 2 450 per night")
    currency_raw: str
    availability: bool
    scraped_at: str        # ISO timestamp
    source_url: str
    extras: dict = field(default_factory=dict)  # any extra channel-specific fields

    def to_dict(self):
        return asdict(self)


class BaseScraper:
    CHANNEL = "base"
    MAX_RETRIES = 2
    RETRY_DELAY = 3  # seconds

    def __init__(self, config: dict):
        self.config = config
        self.scrape_cfg = config["scrape_config"]
        self.logger = logging.getLogger(self.__class__.__name__)

    def _build_dates(self):
        ci = datetime.today() + timedelta(days=self.scrape_cfg["check_in_offset_days"])
        co = ci + timedelta(days=self.scrape_cfg["nights"])
        return ci.strftime("%Y-%m-%d"), co.strftime("%Y-%m-%d")

    async def _launch_browser(self, playwright) -> Browser:
        return await playwright.chromium.launch(
            headless=self.scrape_cfg.get("headless", True),
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ]
        )

    async def _new_stealth_page(self, browser: Browser) -> Page:
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-ZA",
            timezone_id="Africa/Johannesburg",
        )
        page = await ctx.new_page()
        # Basic anti-detection: remove webdriver flag
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
        return page

    async def scrape(self, search_term: str) -> list[RawRate]:
        """Override in subclass. Returns list of RawRate objects."""
        raise NotImplementedError

    async def run(self, search_term: str) -> list[dict]:
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                async with async_playwright() as pw:
                    results = await self.scrape(search_term)
                    self.logger.info(f"[{self.CHANNEL}] scraped {len(results)} rates for '{search_term}'")
                    return [r.to_dict() for r in results]
            except Exception as e:
                self.logger.warning(f"[{self.CHANNEL}] attempt {attempt+1} failed: {e}")
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.RETRY_DELAY)
                else:
                    self.logger.error(f"[{self.CHANNEL}] all retries exhausted for '{search_term}'")
                    return []
