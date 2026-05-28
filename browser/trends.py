from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Page

from browser.helpers import find_element

logger = logging.getLogger(__name__)


async def _dismiss_existing_trend(page: Page):
    """Remove any already-selected trend/template card."""
    close_icons = page.locator('ks-icon-close')
    count = await close_icons.count()
    for i in range(count):
        try:
            if await close_icons.nth(i).is_visible(timeout=1000):
                await close_icons.nth(i).click(force=True)
                await asyncio.sleep(1)
                logger.info("Dismissed existing trend/template card")
                return
        except Exception:
            continue


async def click_tiktok_trend(page: Page):
    """Click the '+ TikTok 趋势' button to open trend panel."""
    await _dismiss_existing_trend(page)
    el = await find_element(page, "tiktok_trend_btn")
    await el.click()
    await asyncio.sleep(1)
    logger.info("Clicked TikTok trend button")


async def open_trend_selector(page: Page):
    """Click 'Select a trend' to open the trend modal, then click '热门广告趋势' tab."""
    select_trend_text = page.locator('text="Select a trend"')
    if await select_trend_text.count() > 0 and await select_trend_text.first.is_visible():
        await select_trend_text.first.click()
        await asyncio.sleep(1)
    else:
        dropdown = page.locator('ks-dropdown-menu-1-1-11:has-text("TikTok")')
        if await dropdown.count() > 0:
            await dropdown.first.click()
            await asyncio.sleep(1)

    modal = page.locator('#inspiration-modal-container')
    for _ in range(5):
        if await modal.count() > 0 and await modal.first.is_visible():
            break
        await asyncio.sleep(1)

    trend_tab = page.locator(':text("热门广告趋势")')
    if await trend_tab.count() > 0:
        await trend_tab.first.click()
        await asyncio.sleep(1)
        logger.info("Clicked '热门广告趋势' tab")


async def select_category_tab(page: Page, category: str):
    """In the trend modal, select category from the first dropdown (行业)."""
    if not category:
        return

    try:
        industry_select = page.locator('#inspiration-modal-container ks-select-1-1-11').first
        if await industry_select.count() == 0:
            logger.warning("Industry dropdown not found, skipping")
            return

        await industry_select.click()
        await asyncio.sleep(1)

        option = page.locator(f':text-is("{category}")')
        if await option.count() > 0:
            try:
                await option.first.click(force=True, timeout=5000)
            except Exception:
                await option.first.dispatch_event("click")
            logger.info("Selected industry: %s", category)
            await asyncio.sleep(1)
        else:
            option2 = page.locator(f':text("{category}")')
            if await option2.count() > 0:
                try:
                    await option2.first.click(force=True, timeout=5000)
                except Exception:
                    await option2.first.dispatch_event("click")
                logger.info("Selected industry (partial): %s", category)
                await asyncio.sleep(1)
            else:
                logger.warning("Industry '%s' not found, pressing Escape", category)
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
    except Exception as e:
        logger.warning("select_category_tab failed: %s, skipping", e)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)


async def select_sub_category(page: Page, sub_category: str):
    """Click sub-category by matching its name text in the left sidebar."""
    if not sub_category:
        return

    clicked = await page.evaluate("""(targetName) => {
        const modal = document.querySelector('#inspiration-modal-container');
        if (!modal) return false;
        const sidebar = modal.querySelector('[class*="flex-shrink-0"][class*="flex-col"]');
        if (!sidebar) return false;
        const scrollList = sidebar.querySelector('[class*="overflow-y-auto"]');
        if (!scrollList) return false;
        const items = scrollList.querySelectorAll('[class*="cursor-pointer"]');
        for (const item of items) {
            const nameEl = item.querySelector('ks-tooltip-1-1-11 div[class*="truncate"]');
            if (nameEl) {
                const text = (nameEl.textContent || '').trim();
                if (text === targetName) {
                    item.click();
                    return true;
                }
            }
        }
        if (/^\\d+$/.test(targetName)) {
            const idx = parseInt(targetName) - 1;
            let subItems = [];
            for (const item of items) {
                if (item.querySelector('ks-tooltip-1-1-11 div[class*="truncate"]')) {
                    subItems.push(item);
                }
            }
            if (idx >= 0 && idx < subItems.length) {
                subItems[idx].click();
                return true;
            }
        }
        return false;
    }""", sub_category)

    if clicked:
        await asyncio.sleep(1.5)
        logger.info("Selected sub-category: %s", sub_category)
    else:
        logger.warning("Sub-category '%s' not found in sidebar", sub_category)


async def select_trend_item(page: Page, trend_index: int):
    """Select the nth trend item (radio button) in the modal."""
    trend_radios = page.locator('#inspiration-modal-scroll-container ks-radio-1-1-11')
    count = await trend_radios.count()
    if count == 0:
        trend_radios = page.locator('#inspiration-modal-container ks-radio-1-1-11')
        count = await trend_radios.count()
    if count == 0:
        raise RuntimeError("No trend items found in modal")
    actual_index = trend_index % count
    await trend_radios.nth(actual_index).click()
    logger.info("Selected trend item %d/%d", actual_index + 1, count)
    await asyncio.sleep(0.5)


async def confirm_trend(page: Page):
    """Click the '选择' confirm button at bottom-right of the trend modal."""
    confirm_btn = page.locator('#inspiration-modal-container ks-button-1-1-11:has-text("选择"), button:has-text("选择")')
    if await confirm_btn.count() > 0:
        await confirm_btn.first.click()
        logger.info("Clicked '选择' confirm button")
    else:
        confirm_btn2 = page.locator('#inspiration-modal-container ks-button-1-1-11').last
        await confirm_btn2.click()
        logger.info("Clicked confirm button (fallback)")
    await asyncio.sleep(1)


async def scrape_all_subcategories(page: Page, categories: list[str]) -> dict[str, list[dict]]:
    """Open trend modal, iterate categories, read sub-category names and translate."""
    from services.translate import translate_batch

    result = {}

    await click_tiktok_trend(page)
    await open_trend_selector(page)
    await asyncio.sleep(2)

    default_names = (await _scrape_sidebar_names(page))[:20]
    if default_names:
        zh_names = translate_batch(default_names)
        result[""] = [{"en": en, "zh": zh} for en, zh in zip(default_names, zh_names)]
        logger.info("Default (no industry): found %d sub-categories", len(default_names))

    for category in categories:
        await select_category_tab(page, category)
        await asyncio.sleep(2)
        names = await _scrape_sidebar_names(page)
        zh_names = translate_batch(names)
        result[category] = [{"en": en, "zh": zh} for en, zh in zip(names, zh_names)]
        logger.info("Category '%s': found %d sub-categories", category, len(names))

    await page.keyboard.press("Escape")
    await asyncio.sleep(1)
    return result


async def _scrape_sidebar_names(page: Page) -> list[str]:
    """Read sub-category names from the left sidebar DOM."""
    return await page.evaluate("""() => {
        const modal = document.querySelector('#inspiration-modal-container');
        if (!modal) return [];
        const sidebar = modal.querySelector('[class*="flex-shrink-0"][class*="flex-col"]');
        if (!sidebar) return [];
        const scrollList = sidebar.querySelector('[class*="overflow-y-auto"]');
        if (!scrollList) return [];
        const items = scrollList.querySelectorAll('[class*="cursor-pointer"]');
        const result = [];
        for (const item of items) {
            const nameEl = item.querySelector('ks-tooltip-1-1-11 div[class*="truncate"]');
            if (nameEl) {
                const text = (nameEl.textContent || '').trim();
                if (text) result.push(text);
            }
        }
        return result;
    }""")
