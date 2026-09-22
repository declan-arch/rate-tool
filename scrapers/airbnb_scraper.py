"""Airbnb scraper for serviced apartments and competing listings."""

import re
from datetime import datetime
from urllib.parse import quote_plus

from playwright.async_api import TimeoutError as PlaywrightTimeout, async_playwright

from .base_scraper import BaseScraper, RawRate


class AirbnbScraper(BaseScraper):
    CHANNEL = "airbnb"

    async def scrape(self, search_term: str) -> list[RawRate]:
        check_in, check_out = self._build_dates()
        adults = self.scrape_cfg["adults"]

        async with async_playwright() as playwright:
            browser = await self._launch_browser(playwright)
            page = await self._new_stealth_page(browser)
            try:
                search_url = (
                    f"https://www.airbnb.com/s/{quote_plus(search_term)}/homes"
                    f"?checkin={check_in}&checkout={check_out}&adults={adults}"
                )
                self.logger.info("[airbnb] searching: %s", search_url)
                await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=self.scrape_cfg["timeout_ms"],
                )
                await page.wait_for_timeout(4000)

                card_selector = None
                for selector in [
                    '[data-testid="card-container"]',
                    '[itemprop="itemListElement"]',
                    '[class*="listingCard"]',
                    '[class*="listing-card"]',
                ]:
                    try:
                        await page.wait_for_selector(selector, timeout=10000)
                        card_selector = selector
                        break
                    except PlaywrightTimeout:
                        continue

                if not card_selector:
                    self.logger.warning("[airbnb] no listing cards found for '%s'", search_term)
                    return []

                rates = []
                scraped_at = datetime.utcnow().isoformat()
                for card in (await page.query_selector_all(card_selector))[:10]:
                    try:
                        property_name = search_term
                        for selector in [
                            '[data-testid="listing-card-title"]',
                            '[class*="title"]',
                            "h3",
                            "h2",
                            "span[aria-label]",
                        ]:
                            element = await card.query_selector(selector)
                            if element:
                                text = (await element.inner_text()).strip()
                                if text:
                                    property_name = text
                                    break

                        room_label = ""
                        for selector in [
                            '[data-testid="listing-card-subtitle"]',
                            '[class*="subtitle"]',
                            '[class*="description"]',
                        ]:
                            element = await card.query_selector(selector)
                            if element:
                                text = (await element.inner_text()).strip()
                                if text:
                                    room_label = text
                                    break

                        price_raw = ""
                        for selector in ['[data-testid="price-availability-row"]', '[class*="price"]']:
                            element = await card.query_selector(selector)
                            if element:
                                price_raw = (await element.inner_text()).strip()
                                if price_raw:
                                    break
                        if not price_raw:
                            match = re.search(r"(?:R|ZAR)\s*([\d\s,]+)", await card.inner_text())
                            if match:
                                price_raw = match.group(0).strip()

                        price_zar = None
                        numbers = re.findall(r"\d+", price_raw.replace(",", "").replace("\xa0", ""))
                        if numbers:
                            price_zar = float(numbers[0])

                        rates.append(RawRate(
                            channel=self.CHANNEL,
                            property_name=property_name,
                            check_in=check_in,
                            check_out=check_out,
                            room_label=room_label or "Listing",
                            rate_label="Per Night",
                            price_zar=price_zar,
                            price_raw=price_raw,
                            currency_raw="ZAR",
                            availability=True,
                            scraped_at=scraped_at,
                            source_url=search_url,
                            extras={},
                        ))
                    except Exception as error:
                        self.logger.debug("[airbnb] card error: %s", error)
                return rates
            except PlaywrightTimeout:
                self.logger.error("[airbnb] timeout for '%s'", search_term)
                return []
            except Exception as error:
                self.logger.error("[airbnb] error: %s", error)
                return []
            finally:
                await browser.close()
