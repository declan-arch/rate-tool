"""
Agoda scraper for SA properties.
"""

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base_scraper import BaseScraper, RawRate


class AgodaScraper(BaseScraper):
    CHANNEL = "agoda"
    BASE_URL = "https://www.agoda.com"

    async def scrape(self, search_term: str) -> list[RawRate]:
        ci, co = self._build_dates()
        adults = self.scrape_cfg["adults"]
        # Agoda date format: MM/DD/YYYY
        ci_agoda = datetime.strptime(ci, "%Y-%m-%d").strftime("%m/%d/%Y")
        co_agoda = datetime.strptime(co, "%Y-%m-%d").strftime("%m/%d/%Y")

        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            page = await self._new_stealth_page(browser)

            try:
                search_url = (
                    f"{self.BASE_URL}/search?"
                    f"city=&textToSearch={quote_plus(search_term)}"
                    f"&checkIn={ci_agoda}&checkOut={co_agoda}"
                    f"&rooms=1&adults={adults}&children=0"
                    f"&currency=ZAR"
                )
                self.logger.info(f"[agoda] searching: {search_url}")
                await page.goto(search_url, wait_until="domcontentloaded",
                                timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(4000)

                # Dismiss cookie/popup
                for close_sel in ['[data-selenium="modal-close-button"]', '#cookie-accept', 'button[aria-label*="close"]']:
                    try:
                        await page.click(close_sel, timeout=2000)
                    except Exception:
                        pass

                # Find first property
                property_link = None
                property_name = search_term
                for sel in [
                    '[data-selenium="hotel-name"] a',
                    '.PropertyCard__Link',
                    'a[data-selenium="hotel-name"]',
                ]:
                    el = await page.query_selector(sel)
                    if el:
                        property_link = await el.get_attribute("href")
                        property_name = (await el.inner_text()).strip()
                        break

                if not property_link:
                    await browser.close()
                    return []

                if not property_link.startswith("http"):
                    property_link = self.BASE_URL + property_link

                await page.goto(property_link, wait_until="domcontentloaded",
                                timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(4000)

                rates = await self._extract_rates(page, property_name, ci, co, property_link)
                await browser.close()
                return rates

            except Exception as e:
                self.logger.error(f"[agoda] error: {e}")
                await browser.close()
                return []

    async def _extract_rates(self, page, property_name, ci, co, source_url) -> list[RawRate]:
        rates = []
        scraped_at = datetime.utcnow().isoformat()

        room_selectors = [
            '[data-selenium="masterroom-title-name"]',
            '.RoomRatePlans__Title',
            '.MasterRoom__Title',
            '[class*="room-name"]',
        ]

        price_selectors = [
            '[data-selenium="display-price"]',
            '.PriceDisplay',
            '[class*="Price__value"]',
            '.price-info',
        ]

        room_els = []
        for sel in room_selectors:
            room_els = await page.query_selector_all(sel)
            if room_els:
                break

        if room_els:
            price_els = []
            for sel in price_selectors:
                price_els = await page.query_selector_all(sel)
                if price_els:
                    break

            for i, room_el in enumerate(room_els[:8]):
                room_label = (await room_el.inner_text()).strip()
                price_raw = ""
                price_zar = None

                if i < len(price_els):
                    price_raw = (await price_els[i].inner_text()).strip()
                    nums = re.findall(r'[\d,]+', price_raw.replace('\xa0', '').replace(' ', '').replace(' ', ''))
                    if nums:
                        try:
                            price_zar = float(nums[0].replace(',', ''))
                        except ValueError:
                            pass

                rates.append(RawRate(
                    channel=self.CHANNEL,
                    property_name=property_name,
                    check_in=ci, check_out=co,
                    room_label=room_label,
                    rate_label="Room Rate",
                    price_zar=price_zar,
                    price_raw=price_raw,
                    currency_raw="ZAR",
                    availability=price_zar is not None,
                    scraped_at=scraped_at,
                    source_url=source_url,
                    extras={}
                ))
        else:
            # Fallback
            for sel in price_selectors:
                els = await page.query_selector_all(sel)
                for el in els[:6]:
                    price_raw = (await el.inner_text()).strip()
                    nums = re.findall(r'[\d,]+', price_raw.replace('\xa0', '').replace(' ', ''))
                    if nums and len(nums[0]) >= 3:
                        try:
                            rates.append(RawRate(
                                channel=self.CHANNEL,
                                property_name=property_name,
                                check_in=ci, check_out=co,
                                room_label="Unknown Room",
                                rate_label="",
                                price_zar=float(nums[0].replace(',', '')),
                                price_raw=price_raw,
                                currency_raw="ZAR",
                                availability=True,
                                scraped_at=scraped_at,
                                source_url=source_url,
                                extras={"fallback": True}
                            ))
                        except ValueError:
                            pass
                if rates:
                    break

        return rates
