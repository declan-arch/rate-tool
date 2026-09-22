"""Nightsbridge scraper — South African booking platform."""

import re
from datetime import datetime
from urllib.parse import quote_plus

from playwright.async_api import TimeoutError as PlaywrightTimeout, async_playwright

from .base_scraper import BaseScraper, RawRate


class NightsbridgeScraper(BaseScraper):
    CHANNEL = "nightsbridge"
    SEARCH_URL = "https://www.nightsbridge.co.za/search"

    async def scrape(self, search_term: str) -> list[RawRate]:
        check_in, check_out = self._build_dates()
        adults = self.scrape_cfg["adults"]

        async with async_playwright() as playwright:
            browser = await self._launch_browser(playwright)
            page = await self._new_stealth_page(browser)
            try:
                search_url = (
                    f"{self.SEARCH_URL}?destination={quote_plus(search_term)}"
                    f"&checkin={check_in}&checkout={check_out}&adults={adults}"
                )
                self.logger.info("[nightsbridge] searching: %s", search_url)
                await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=self.scrape_cfg["timeout_ms"],
                )

                card_selector = None
                for selector in [
                    ".property-card",
                    ".search-result",
                    '[class*="property"]',
                    '[class*="result"]',
                ]:
                    try:
                        await page.wait_for_selector(selector, timeout=20000)
                        card_selector = selector
                        break
                    except PlaywrightTimeout:
                        continue

                if not card_selector:
                    self.logger.warning("[nightsbridge] no result cards found for '%s'", search_term)
                    return []

                rates = []
                scraped_at = datetime.utcnow().isoformat()
                for card in (await page.query_selector_all(card_selector))[:10]:
                    try:
                        property_name = search_term
                        for selector in ["h2", "h3", ".property-name", ".name", '[class*="name"]', '[class*="title"]']:
                            element = await card.query_selector(selector)
                            if element:
                                text = (await element.inner_text()).strip()
                                if text:
                                    property_name = text
                                    break

                        room_label = ""
                        for selector in [".room-name", ".room-type", ".unit-name", '[class*="room"]', '[class*="unit"]']:
                            element = await card.query_selector(selector)
                            if element:
                                text = (await element.inner_text()).strip()
                                if text:
                                    room_label = text
                                    break

                        price_raw = ""
                        for selector in [".price", ".rate", '[class*="price"]', '[class*="rate"]']:
                            element = await card.query_selector(selector)
                            if element:
                                price_raw = (await element.inner_text()).strip()
                                if price_raw:
                                    break
                        if not price_raw:
                            match = re.search(r"R\s*([\d\s,]+)", await card.inner_text())
                            if match:
                                price_raw = match.group(0).strip()

                        price_zar = None
                        numbers = re.findall(r"\d+", price_raw.replace(",", "").replace("\xa0", ""))
                        if numbers:
                            price_zar = float(numbers[0])

                        availability_text = ""
                        for selector in [".availability", '[class*="avail"]', ".status"]:
                            element = await card.query_selector(selector)
                            if element:
                                availability_text = (await element.inner_text()).strip().lower()
                                break

                        rates.append(RawRate(
                            channel=self.CHANNEL,
                            property_name=property_name,
                            check_in=check_in,
                            check_out=check_out,
                            room_label=room_label or "Room",
                            rate_label="Nightly Rate",
                            price_zar=price_zar,
                            price_raw=price_raw,
                            currency_raw="ZAR",
                            availability="unavail" not in availability_text and "sold out" not in availability_text,
                            scraped_at=scraped_at,
                            source_url=search_url,
                            extras={},
                        ))
                    except Exception as error:
                        self.logger.debug("[nightsbridge] card error: %s", error)
                return rates
            except PlaywrightTimeout:
                self.logger.error("[nightsbridge] timeout for '%s'", search_term)
                return []
            except Exception as error:
                self.logger.error("[nightsbridge] error: %s", error)
                return []
            finally:
                await browser.close()
