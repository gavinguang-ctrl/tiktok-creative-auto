from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from playwright.async_api import Page, Locator

from config import SELECTORS, STEP_RETRY_COUNT, STEP_RETRY_DELAYS

logger = logging.getLogger(__name__)

OnProgress = Callable[[str], Awaitable[None]]


async def find_element(page: Page, selector_key: str) -> Locator:
    """Try multiple selectors from SELECTORS config, return first visible match."""
    selectors = SELECTORS[selector_key].split(", ")
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible(timeout=2000):
                return loc.first
        except Exception:
            continue
    raise RuntimeError(f"Cannot find element for '{selector_key}': tried {selectors}")


async def retry(coro_fn, description: str):
    """Retry an async operation with configured delays."""
    last_error = None
    for attempt in range(STEP_RETRY_COUNT):
        try:
            return await coro_fn()
        except Exception as e:
            last_error = e
            delay = STEP_RETRY_DELAYS[min(attempt, len(STEP_RETRY_DELAYS) - 1)]
            logger.warning("%s attempt %d failed: %s. Retry in %.1fs", description, attempt + 1, e, delay)
            await asyncio.sleep(delay)
    raise RuntimeError(f"{description} failed after {STEP_RETRY_COUNT} attempts: {last_error}")


async def dismiss_modal(page: Page):
    """Close any modal/overlay that might be blocking the page."""
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.5)
    modal = page.locator("#inspiration-modal-container, .KsModal")
    if await modal.count() > 0:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)


async def cdp_click(page: Page, x: float, y: float):
    """Click at coordinates using CDP Input.dispatchMouseEvent."""
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})


async def cdp_click_element(page: Page, js_get_box: str, *args) -> bool:
    """Evaluate JS to get {x, y} coordinates, then CDP click. Returns True if clicked."""
    box = await page.evaluate(js_get_box, *args) if args else await page.evaluate(js_get_box)
    if not box:
        return False
    await cdp_click(page, box["x"], box["y"])
    return True
