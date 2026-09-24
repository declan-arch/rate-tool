"""
Direct website scraper — best-effort rate extraction from a hotel's own booking
site. Unlike the OTA scrapers, there's no shared DOM to target (every property
runs a different CMS/booking engine), so this scans the rendered page for
currency-formatted prices near booking-related content instead of relying on
fixed selectors.

Used for both the target property (name known) and competitors (name derived
from the page itself, since competitors are entered as URLs only).
"""

import asyncio
import re
from datetime import datetime
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base_scraper import BaseScraper, RawRate

PRICE_PATTERN = re.compile(r"(?:R|ZAR)\s?([\d][\d,\s]{2,7}(?:\.\d{2})?)\b", re.IGNORECASE)
BOOKING_KEYWORDS = re.compile(
    r"book|rate|room|night|stay|availab|reserv|price|check.?in|tariff|pay|total|deal|offer",
    re.IGNORECASE,
)
# Add-on/upsell pricing (dinner vouchers, room upgrades, extras) matches the booking
# keywords above just as easily as the actual room rate — exclude it explicitly so it
# doesn't get picked up ahead of the real rate in document order.
UPSELL_KEYWORDS = re.compile(
    r"voucher|upgrade|add.?on|addon|insurance|supplement|optional|personalise|personalize|"
    r"per adult|per person|add a|extra choice|parking|breakfast add|spa treatment",
    re.IGNORECASE,
)
# Booking-engine pages (e.g. a hosted results/checkout step) often carry a generic step
# title like "Guests and Extras" instead of the hotel's name — reject titles made up
# entirely of these words and fall back to the domain instead.
GENERIC_TITLE_WORDS = {
    "guests", "extras", "and", "checkout", "results", "result", "search", "availability",
    "book", "now", "confirm", "confirmation", "payment", "reservation", "reservations",
    "cart", "summary", "booking", "step", "details", "select", "choose", "review", "your",
    "room",
}
# Whole-phrase generic titles a booking engine commonly reuses across every property —
# checked before the word-level check since some individual words above (e.g. "room")
# are too common to safely blacklist on their own without this exact-phrase net.
GENERIC_TITLE_PHRASES = {
    "room reservations", "guests and extras", "booking engine", "availability",
    "check availability", "reserve now", "your booking", "confirm your stay",
    "select your room", "room selection", "book now", "checkout", "reservation",
    "reservations", "booking", "results", "search results",
}
COMMON_SUBDOMAIN_PREFIXES = {"www", "book", "booking", "reservations", "reservation", "res", "stay", "stays", "hotel", "hotels", "secure", "app"}


class DirectScraper(BaseScraper):
    CHANNEL = "direct"

    async def run(self, url: str, property_name: str = None) -> list[dict]:
        """Overrides BaseScraper.run() to carry an optional property_name through —
        known for the target property, derived from the page for competitors."""
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                results = await self.scrape(url, property_name)
                self.logger.info(f"[{self.CHANNEL}] scraped {len(results)} rates for '{url}'")
                return [r.to_dict() for r in results]
            except Exception as e:
                self.logger.warning(f"[{self.CHANNEL}] attempt {attempt + 1} failed: {e}")
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.RETRY_DELAY)
                else:
                    self.logger.error(f"[{self.CHANNEL}] all retries exhausted for '{url}'")
                    return []

    async def scrape(self, url: str, property_name: str = None) -> list[RawRate]:
        ci, co = self._build_dates()
        scraped_at = datetime.utcnow().isoformat()

        if not url or not url.strip():
            return []
        if not url.startswith("http"):
            url = "https://" + url

        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            page = await self._new_stealth_page(browser)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(3000)

                # Try extracting from the page as-loaded first. Only fall back to clicking
                # around (cookie banners, "Book Now") if that finds nothing — a click can
                # reset a page that was already showing real rates (seen in practice: a
                # broad cookie-banner selector matched an unrelated widget control and
                # reset a booking engine back to its date-picker step, wiping out prices
                # that were already visible).
                resolved_name = property_name or await self._derive_property_name(page, url)
                rates = await self._extract_heuristic_prices(page, resolved_name, ci, co, url, scraped_at)

                if not rates:
                    for sel in ['[id*="cookie" i] button', 'button[aria-label*="accept" i]']:
                        try:
                            await page.click(sel, timeout=1500)
                        except Exception:
                            pass
                    for sel in ['a:has-text("Book Now")', 'a:has-text("Check Availability")',
                                'button:has-text("Book Now")', 'button:has-text("Check Rates")']:
                        try:
                            await page.click(sel, timeout=2000)
                            await page.wait_for_timeout(3000)
                            break
                        except Exception:
                            continue
                    resolved_name = property_name or await self._derive_property_name(page, url)
                    rates = await self._extract_heuristic_prices(page, resolved_name, ci, co, url, scraped_at)

                await browser.close()
                return rates
            except PlaywrightTimeout:
                self.logger.error(f"[direct] timeout loading '{url}'")
                await browser.close()
                return []
            except Exception as e:
                self.logger.error(f"[direct] unexpected error: {e}")
                await browser.close()
                return []

    async def _derive_property_name(self, page, url: str) -> str:
        """No name is supplied for competitor URLs — best-effort label from the
        page's own <title>, falling back to the domain name if every segment of the
        title looks like a generic booking-flow step (e.g. "Guests and Extras",
        "Room Reservations") rather than a hotel name. Titles like "Room Reservations
        - The Capital Melrose" put the generic part first, so every segment is
        checked, not just the first."""
        try:
            title = (await page.title()).strip()
            candidates = [title]
            for sep in [" | ", " – ", " — ", " - ", " :: "]:
                if sep in title:
                    candidates = [p.strip() for p in title.split(sep)]
                    break
            for candidate in candidates:
                if not (candidate and 2 <= len(candidate) <= 80):
                    continue
                normalized = candidate.lower().strip()
                if normalized in GENERIC_TITLE_PHRASES:
                    continue
                words = set(re.findall(r"[a-z]+", normalized))
                if words and words.issubset(GENERIC_TITLE_WORDS):
                    continue
                return candidate
        except Exception:
            pass
        return self._domain_to_name(url)

    def _domain_to_name(self, url: str) -> str:
        parts = [p for p in (urlparse(url).netloc or url).split(".") if p]
        while parts and parts[0].lower() in COMMON_SUBDOMAIN_PREFIXES:
            parts.pop(0)
        brand = parts[0] if parts else (urlparse(url).netloc or url)
        return brand.replace("-", " ").title() or "Competitor (direct site)"

    async def _extract_heuristic_prices(self, page, property_name, ci, co, source_url, scraped_at) -> list[RawRate]:
        """Scan the page for currency-formatted numbers near booking-related text.
        Low-confidence by nature — every hotel's site is structured differently."""
        rates = []
        body_text = await page.inner_text("body")
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]

        seen_prices = set()
        for i, line in enumerate(lines):
            for match in PRICE_PATTERN.finditer(line):
                raw_num = match.group(1).replace(",", "").replace(" ", "")
                try:
                    price_zar = float(raw_num)
                except ValueError:
                    continue
                if price_zar < 200 or price_zar > 50000:
                    continue  # implausible for a per-night hotel rate — likely noise
                if price_zar in seen_prices:
                    continue

                context = " ".join(lines[max(0, i - 2):i + 1])
                if not BOOKING_KEYWORDS.search(context):
                    continue  # price-looking number with no booking context nearby
                if UPSELL_KEYWORDS.search(context):
                    continue  # add-on/upsell price (dinner voucher, room upgrade, etc.), not the room rate

                seen_prices.add(price_zar)
                rates.append(RawRate(
                    channel=self.CHANNEL,
                    property_name=property_name,
                    check_in=ci,
                    check_out=co,
                    room_label=context[:120],
                    rate_label="Direct website (unverified)",
                    price_zar=price_zar,
                    price_raw=match.group(0),
                    currency_raw="ZAR",
                    availability=True,
                    scraped_at=scraped_at,
                    source_url=source_url,
                    extras={"heuristic": True, "confidence": "LOW"},
                ))
                if len(rates) >= 10:
                    return rates

        return rates
