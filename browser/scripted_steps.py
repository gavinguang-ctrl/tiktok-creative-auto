from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from playwright.async_api import Page, Locator

from config import SELECTORS, STEP_RETRY_COUNT, STEP_RETRY_DELAYS
from models.schemas import InputData
from services.template import render_prompt

logger = logging.getLogger(__name__)

OnProgress = Callable[[str], Awaitable[None]]


async def _find_element(page: Page, selector_key: str) -> Locator:
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


async def _retry(coro_fn, description: str):
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


async def click_new_chat(page: Page):
    """Click '+ 开启新对话' in the left sidebar."""
    el = await _find_element(page, "new_chat")
    await el.click()
    await asyncio.sleep(3)
    # Wait for the new chat page to load (URL changes to /chat without ID)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(2)
    logger.info("Clicked new chat, page: %s", page.url)


async def _dismiss_modal(page: Page):
    """Close any modal/overlay that might be blocking the page."""
    # Press Escape to dismiss modals
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.5)
    # Also try clicking outside any modal
    modal = page.locator("#inspiration-modal-container, .KsModal")
    if await modal.count() > 0:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    logger.info("Dismissed any blocking modals")


async def fill_prompt(page: Page, prompt: str):
    """Fill the prompt text into the chat input box."""
    await _dismiss_modal(page)
    el = await _find_element(page, "chat_input")
    await el.click()
    await asyncio.sleep(0.3)
    # Clear existing content using keyboard (execCommand doesn't work on ProseMirror)
    await page.keyboard.press("Control+A")
    await asyncio.sleep(0.2)
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.3)
    await page.keyboard.press("Control+A")
    await asyncio.sleep(0.1)
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.3)
    # Insert new prompt via clipboard paste (fast, works on ProseMirror)
    await page.evaluate("(text) => navigator.clipboard.writeText(text)", prompt)
    await page.keyboard.press("Control+V")
    await asyncio.sleep(0.5)
    logger.info("Prompt filled (%d chars)", len(prompt))


async def _clear_all_images(page: Page):
    """Remove all uploaded image attachments by clicking their close buttons."""
    # Images appear as 60x60 thumbnail cards with a close button (ks-icon-close-small)
    # inside a button.absolute.right-1.top-1 parent
    for attempt in range(20):  # max 20 images
        close_btns = page.locator('button.absolute.right-1.top-1:has(ks-icon-close-small)')
        count = await close_btns.count()
        if count == 0:
            break
        try:
            await close_btns.first.click()
            await asyncio.sleep(0.3)
        except Exception:
            break
    # Also try clearing any images inside the contenteditable editor (fallback)
    await page.evaluate("""() => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) return;
        const imgNodes = editor.querySelectorAll(
            '[data-type*="image"], [contenteditable="false"]:not(.placeholder), img'
        );
        imgNodes.forEach(el => el.remove());
    }""")




async def paste_images(page: Page, image_paths: list[str]):
    """Upload images by simulating clipboard paste. If some get stuck, remove them."""
    if not image_paths:
        logger.info("No images to upload, skipping")
        return

    await _dismiss_modal(page)
    input_el = await _find_element(page, "chat_input")
    await input_el.click()
    await asyncio.sleep(0.5)

    import base64
    images_data = []
    for img_path in image_paths:
        try:
            with open(img_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            ext = img_path.lower().rsplit(".", 1)[-1]
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")
            filename = img_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            images_data.append((img_b64, mime, filename))
        except Exception as e:
            logger.warning("Cannot read image %s: %s", img_path, e)

    # Paste all images
    for img_b64, mime, filename in images_data:
        await _paste_single_image(page, img_b64, mime, filename)
        await asyncio.sleep(1.5)

    # Wait for uploads to complete (send button becomes enabled)
    await asyncio.sleep(5)
    for check in range(10):
        if await _is_send_enabled(page):
            logger.info("All %d images uploaded (send button enabled)", len(images_data))
            return
        logger.info("Send button still disabled, images uploading... (%d/10)", check + 1)
        await asyncio.sleep(3)

    # Some images are stuck — remove them one by one until send button is enabled
    logger.warning("Some images stuck uploading after 35s, removing stuck ones")
    for _ in range(len(images_data)):
        await _remove_one_stuck_image(page)
        await asyncio.sleep(1)
        if await _is_send_enabled(page):
            logger.info("Send button enabled after removing stuck images")
            return

    logger.warning("Send button still disabled after removing images")


async def _is_send_enabled(page: Page) -> bool:
    """Check if the send button is currently enabled."""
    return not await page.evaluate("""() => {
        const sendBtn = document.querySelector('ks-icon-arrow-up-small');
        if (!sendBtn) return true;
        const btn = sendBtn.closest('ks-icon-button-1-1-11') || sendBtn.closest('button');
        if (!btn) return true;
        return btn.hasAttribute('disabled') || btn.getAttribute('aria-disabled') === 'true' ||
               btn.classList.contains('disabled') || getComputedStyle(btn).opacity < 0.5 ||
               getComputedStyle(btn).pointerEvents === 'none';
    }""")


async def _remove_one_stuck_image(page: Page):
    """Remove one uploading/stuck image. Stuck images have blob: src (not yet uploaded to server)."""
    removed = await page.evaluate("""() => {
        const closeBtns = document.querySelectorAll('button.absolute.right-1.top-1');
        for (let i = closeBtns.length - 1; i >= 0; i--) {
            const btn = closeBtns[i];
            const container = btn.closest('[class*="relative"]') || btn.parentElement;
            if (!container) continue;
            const img = container.querySelector('img');
            // Stuck/uploading images have blob: or data: src, completed ones have https:
            if (img && (img.src.startsWith('blob:') || img.src.startsWith('data:'))) {
                btn.click();
                return true;
            }
            // Also remove if there's a loading spinner
            if (container.querySelector('[class*="animate"], [class*="loading"], [class*="progress"]')) {
                btn.click();
                return true;
            }
        }
        // Fallback: if no blob/loading found but send still disabled, remove last one
        if (closeBtns.length > 0) {
            closeBtns[closeBtns.length - 1].click();
            return true;
        }
        return false;
    }""")
    if removed:
        await asyncio.sleep(0.5)


async def _paste_single_image(page: Page, img_b64: str, mime: str, filename: str):
    """Dispatch a paste event with one image."""
    await page.evaluate("""([imgBase64, mimeType, fileName]) => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) return;
        editor.focus();

        const byteChars = atob(imgBase64);
        const byteArray = new Uint8Array(byteChars.length);
        for (let i = 0; i < byteChars.length; i++) {
            byteArray[i] = byteChars.charCodeAt(i);
        }
        const blob = new Blob([byteArray], { type: mimeType });
        const file = new File([blob], fileName, { type: mimeType });

        const dt = new DataTransfer();
        dt.items.add(file);

        const pasteEvent = new ClipboardEvent('paste', {
            bubbles: true,
            cancelable: true,
            clipboardData: dt
        });
        editor.dispatchEvent(pasteEvent);
    }""", [img_b64, mime, filename])


async def _dismiss_existing_trend(page: Page):
    """Remove any already-selected trend/template card.
    The card's × button is the first visible <ks-icon-close> element on the page."""
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
    el = await _find_element(page, "tiktok_trend_btn")
    await el.click()
    await asyncio.sleep(1)
    logger.info("Clicked TikTok trend button")


async def open_trend_selector(page: Page):
    """Click 'Select a trend' to open the trend modal, then click '热门广告趋势' tab."""
    # Try clicking "Select a trend" or the dropdown trigger
    select_trend_text = page.locator('text="Select a trend"')
    if await select_trend_text.count() > 0 and await select_trend_text.first.is_visible():
        await select_trend_text.first.click()
        await asyncio.sleep(1)
        logger.info("Opened trend selector modal via 'Select a trend'")
    else:
        # Might already show a selected trend name - click the dropdown area
        dropdown = page.locator('ks-dropdown-menu-1-1-11:has-text("TikTok")')
        if await dropdown.count() > 0:
            await dropdown.first.click()
            await asyncio.sleep(1)

    # Wait for modal to appear
    modal = page.locator('#inspiration-modal-container')
    for _ in range(5):
        if await modal.count() > 0 and await modal.first.is_visible():
            break
        await asyncio.sleep(1)

    # Click '热门广告趋势' tab
    trend_tab = page.locator(':text("热门广告趋势")')
    if await trend_tab.count() > 0:
        await trend_tab.first.click()
        await asyncio.sleep(1)
        logger.info("Clicked '热门广告趋势' tab")
    else:
        logger.warning("'热门广告趋势' tab not found")


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

        # Select the category from dropdown options (use force click, don't rely on visibility)
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


async def _get_subcategory_sidebar(page: Page):
    """Get the left sidebar container (flex-shrink-0 flex-col) inside the trend modal."""
    sidebar = page.locator('#inspiration-modal-container [class*="flex-shrink-0"][class*="flex-col"]')
    if await sidebar.count() == 0:
        return None
    return sidebar.first


async def _get_subcategory_items(page: Page):
    """Get all clickable sub-category items from the left sidebar."""
    sidebar = await _get_subcategory_sidebar(page)
    if not sidebar:
        return page.locator('__nonexistent__'), 0
    # The scrollable list is the second child (first is the "热门广告趋势" header)
    scroll_list = sidebar.locator('[class*="overflow-y-auto"]')
    if await scroll_list.count() == 0:
        return page.locator('__nonexistent__'), 0
    # Each item is a div with cursor-pointer class (skip the first "所有趋势" item)
    items = scroll_list.locator('[class*="cursor-pointer"]')
    count = await items.count()
    return items, count


async def select_sub_category(page: Page, sub_category: str):
    """Click sub-category by matching its name text in the left sidebar."""
    if not sub_category:
        return

    # Find and click the matching sub-category item via evaluate + click
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
        // Fallback: if targetName is a number, click by index (1-based)
        if (/^\\d+$/.test(targetName)) {
            const idx = parseInt(targetName) - 1;
            // Skip items without ks-tooltip (like "所有趋势" header)
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
                if (text) {
                    result.push(text);
                }
            }
        }
        return result;
    }""")


async def scrape_all_subcategories(page: Page, categories: list[str]) -> dict[str, list[dict]]:
    """Open trend modal, iterate categories, read sub-category names and translate to Chinese."""
    from services.translate import translate_batch

    result = {}

    await click_tiktok_trend(page)
    await open_trend_selector(page)
    await asyncio.sleep(2)

    # First: scrape default sub-categories (no industry selected, limit 20)
    default_names = (await _scrape_sidebar_names(page))[:20]
    if default_names:
        zh_names = translate_batch(default_names)
        result[""] = [{"en": en, "zh": zh} for en, zh in zip(default_names, zh_names)]
        logger.info("Default (no industry): found %d sub-categories", len(default_names))

    # Then: scrape each industry
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


async def select_trend_item(page: Page, trend_index: int):
    """Select the nth trend item (radio button) in the modal."""
    # Try primary selector
    trend_radios = page.locator('#inspiration-modal-scroll-container ks-radio-1-1-11')
    count = await trend_radios.count()
    # Fallback: try broader selector if primary finds nothing
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
    # The button text is "选择" (green button at bottom-right)
    confirm_btn = page.locator('#inspiration-modal-container ks-button-1-1-11:has-text("选择"), button:has-text("选择")')
    if await confirm_btn.count() > 0:
        await confirm_btn.first.click()
        logger.info("Clicked '选择' confirm button")
    else:
        # Fallback: last button in modal
        confirm_btn2 = page.locator('#inspiration-modal-container ks-button-1-1-11').last
        await confirm_btn2.click()
        logger.info("Clicked confirm button (fallback)")
    await asyncio.sleep(1)


async def click_send(page: Page):
    """Click the send/submit button (arrow up icon)."""
    send_btn = page.locator('ks-icon-arrow-up-small')
    if await send_btn.count() > 0 and await send_btn.first.is_visible():
        await send_btn.first.click()
    else:
        # Fallback: try the send button area
        send_btn2 = page.locator('ks-icon-button-1-1-11:has(ks-icon-arrow-up-small)')
        if await send_btn2.count() > 0:
            await send_btn2.first.click()
    logger.info("Clicked send button")
    await asyncio.sleep(2)


async def click_first_reply(page: Page):
    """Click the first reply/suggestion button in the conversation."""
    # Reply buttons have this specific class pattern
    reply_btn = page.locator('button.tiktok-bodySm.rounded-full.border')
    if await reply_btn.count() > 0:
        await reply_btn.first.click()
        logger.info("Clicked first reply button")
        await asyncio.sleep(2)
        return True
    return False


async def _handle_rejection_and_retry(page: Page, rejection_count: int = 0) -> bool:
    """Check if video was rejected (AI safety violation). If so, handle recovery.
    First rejection: click storyboard/refine option.
    Subsequent rejections: click generate video option (not storyboard again).
    Returns True if rejection was detected and handled."""
    # Detect rejection indicators
    rejection = page.locator(':text("违反"), :text("rejected"), :text("audit"), :text("安全规范")')
    if await rejection.count() == 0:
        return False

    logger.warning("Video rejection detected (count=%d), attempting recovery", rejection_count)

    reply_btns = page.locator('button.tiktok-bodySm.rounded-full.border')
    count = await reply_btns.count()
    if count == 0:
        return False

    if rejection_count == 0:
        # First rejection: choose storyboard/refine (usually second button)
        if count >= 2:
            for i in range(count):
                text = (await reply_btns.nth(i).text_content() or "").lower()
                if "storyboard" in text or "refine" in text or "rewrite" in text:
                    await reply_btns.nth(i).click()
                    logger.info("First rejection: clicked refine option: %s", text[:50])
                    await asyncio.sleep(5)
                    return True
            await reply_btns.nth(1).click()
            logger.info("First rejection: clicked second button")
            await asyncio.sleep(5)
            return True
        else:
            await reply_btns.first.click()
            await asyncio.sleep(5)
            return True
    else:
        # Subsequent rejections: choose generate video (usually first button)
        for i in range(count):
            text = (await reply_btns.nth(i).text_content() or "").lower()
            if "generate" in text or "video" in text or "生成" in text:
                await reply_btns.nth(i).click()
                logger.info("Subsequent rejection: clicked generate option: %s", text[:50])
                await asyncio.sleep(5)
                return True
        # Fallback: click first button (generate video is typically first)
        await reply_btns.first.click()
        logger.info("Subsequent rejection: clicked first button")
        await asyncio.sleep(5)
        return True


async def wait_until_generating(page: Page, timeout_s: int = 300, stuck_threshold_s: int = 180):
    """Keep clicking first reply until '正在生成视频' appears.
    If rejection is detected, handle it and continue retrying.
    Returns False if stuck for stuck_threshold_s without any progress."""
    elapsed = 0
    last_action_at = 0
    rejection_count = 0
    while elapsed < timeout_s:
        # Check if generation started
        generating = page.locator(':text("正在生成视频")')
        if await generating.count() > 0:
            logger.info("Video generation started")
            return True

        # Check for rejection/error and handle it
        if await _handle_rejection_and_retry(page, rejection_count):
            rejection_count += 1
            last_action_at = elapsed
            elapsed += 5
            continue

        # Click first reply button if available
        reply_btn = page.locator('button.tiktok-bodySm.rounded-full.border')
        try:
            if await reply_btn.count() > 0 and await reply_btn.first.is_visible(timeout=2000):
                await reply_btn.first.click()
                logger.info("Clicked reply button")
                last_action_at = elapsed
                await asyncio.sleep(5)
                elapsed += 5
            else:
                await asyncio.sleep(5)
                elapsed += 5
        except Exception:
            await asyncio.sleep(5)
            elapsed += 5

        # Stuck detection
        if elapsed - last_action_at >= stuck_threshold_s:
            logger.warning("Stuck for %ds without progress, aborting", stuck_threshold_s)
            return False

    logger.warning("Timeout waiting for generation after %ds", timeout_s)
    return False


async def poll_and_advance_conversations(page: Page, conversation_count: int, on_progress=None, timeout_s: int = 600):
    """Poll all recent conversations, click reply buttons until all reach '正在生成视频'.

    Strategy: cycle through the N most recent conversations in the left sidebar,
    click any visible reply button, check for '正在生成视频' status.
    """
    generating_set = set()
    elapsed = 0
    poll_interval = 8

    while elapsed < timeout_s and len(generating_set) < conversation_count:
        # Get sidebar conversation items (div with cursor-pointer and gap-4)
        sidebar_items = page.locator('div.cursor-pointer.items-center.gap-4')
        count = await sidebar_items.count()

        for i in range(min(conversation_count, count)):
            if i in generating_set:
                continue

            # Click this conversation
            try:
                await sidebar_items.nth(i).click()
                await asyncio.sleep(3)
            except Exception:
                continue

            # Check if already generating
            generating = page.locator(':text("正在生成视频")')
            if await generating.count() > 0:
                generating_set.add(i)
                logger.info("Conversation %d is generating", i + 1)
                continue

            # Click first reply button if available
            reply_btn = page.locator('button.tiktok-bodySm.rounded-full.border')
            try:
                if await reply_btn.count() > 0 and await reply_btn.first.is_visible(timeout=2000):
                    await reply_btn.first.click()
                    logger.info("Clicked reply in conversation %d", i + 1)
                    await asyncio.sleep(3)
            except Exception:
                pass

        if on_progress:
            await on_progress(f"轮询中: {len(generating_set)}/{conversation_count} 已进入生成状态")

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.info("All %d/%d conversations generating", len(generating_set), conversation_count)


async def download_all_videos(
    page: Page,
    task_id: str,
    task_started_at,
    expected_count: int,
    on_progress=None,
):
    """Poll the history page every 3 minutes, download videos created after task_started_at.

    Identifies "our" tasks by card creation timestamp (UTC, encoded in card name).
    Handles failed tasks: clicks 三点 → 在聊天中查找 → 点击回复 to retry once.
    """
    from config import DOWNLOADS_DIR, HISTORY_URL
    from datetime import datetime, timedelta

    DOWNLOADS_DIR.mkdir(exist_ok=True)
    results: list[str] = []
    downloaded_ids: set[str] = set()
    permanently_failed_ids: set[str] = set()
    retried_ids: set[str] = set()

    # Window: cards whose name timestamp >= task_started_at - 60s buffer (UTC)
    window_start_utc = (task_started_at.astimezone() - timedelta(seconds=60)).utctimetuple()
    window_start_str = "{:04d}{:02d}{:02d}{:02d}{:02d}{:02d}".format(
        window_start_utc.tm_year, window_start_utc.tm_mon, window_start_utc.tm_mday,
        window_start_utc.tm_hour, window_start_utc.tm_min, window_start_utc.tm_sec,
    )
    logger.info("Download window starts (UTC): %s, expected count: %d", window_start_str, expected_count)

    poll_interval = 180  # 3 minutes
    max_wait = 5400      # 90 minutes
    elapsed = 0

    await page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(8)

    while elapsed < max_wait:
        completed = len(downloaded_ids) + len(permanently_failed_ids)
        if completed >= expected_count:
            break

        if on_progress:
            await on_progress(
                f"轮询下载: 已完成 {len(downloaded_ids)}/{expected_count}, 失败 {len(permanently_failed_ids)}"
            )

        # Reload to get latest status
        try:
            await page.reload(wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning("Reload failed: %s", e)
        await asyncio.sleep(8)

        # Extract our cards via JS (sidebar tab may render at 0x0 in background, but DOM still works)
        cards_info = await page.evaluate("""(windowStart) => {
            const out = [];
            const cards = document.querySelectorAll('div.cursor-pointer.items-center.gap-4');
            for (const c of cards) {
                const txt = (c.innerText || '').trim();
                const m = txt.match(/Creative agent_(\\d{14})/);
                if (!m) continue;
                if (m[1] < windowStart) continue;  // older than our task window
                out.push({ id: m[1], text: txt.substring(0, 200) });
            }
            return out;
        }""", window_start_str)

        logger.info("Found %d cards in window", len(cards_info))

        for card_info in cards_info:
            card_id = card_info["id"]
            if card_id in downloaded_ids or card_id in permanently_failed_ids:
                continue

            # Click the card via JS (avoids visibility issues from background tab)
            clicked = await page.evaluate("""(cardId) => {
                const cards = document.querySelectorAll('div.cursor-pointer.items-center.gap-4');
                for (const c of cards) {
                    const txt = c.innerText || '';
                    if (txt.includes('Creative agent_' + cardId)) {
                        c.scrollIntoView({block: 'center'});
                        c.click();
                        return true;
                    }
                }
                return false;
            }""", card_id)

            if not clicked:
                continue
            await asyncio.sleep(3)

            # Detect status from detail panel
            status = await page.evaluate("""() => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const txt = (el.innerText || '').trim();
                    if (el.children.length === 0) {
                        if (txt === '已导出') return 'exported';
                        if (txt === '失败') return 'failed';
                        if (txt === '生成中') return 'generating';
                    }
                }
                return 'unknown';
            }""")

            logger.info("Card %s status: %s", card_id, status)

            if status == "exported":
                # Find and click download button
                save_path = str(DOWNLOADS_DIR / f"{task_id}_{len(results)+1}.mp4")
                download_btn = page.locator('button:has-text("下载")').filter(has_not_text="同步")
                try:
                    if await download_btn.count() > 0:
                        async with page.expect_download(timeout=60000) as dl_info:
                            await download_btn.first.click()
                        download = await dl_info.value
                        await download.save_as(save_path)
                        results.append(save_path)
                        downloaded_ids.add(card_id)
                        logger.info("Downloaded card %s to %s", card_id, save_path)
                        if on_progress:
                            await on_progress(f"已下载 {len(downloaded_ids)}/{expected_count} 个视频")
                except Exception as e:
                    logger.warning("Download failed for %s: %s", card_id, e)

            elif status == "failed":
                if card_id in retried_ids:
                    permanently_failed_ids.add(card_id)
                    logger.info("Card %s permanently failed (already retried)", card_id)
                else:
                    retried_ids.add(card_id)
                    logger.info("Card %s failed, attempting recovery", card_id)
                    if on_progress:
                        await on_progress(f"任务 {card_id} 失败，正在重试...")
                    try:
                        await _recover_failed_task(page)
                    except Exception as e:
                        logger.warning("Recovery failed for %s: %s", card_id, e)
                    # _recover_failed_task navigates back to history; refresh state
                    await asyncio.sleep(3)
                    continue  # next card

            # status == "generating" or "unknown": skip this round, wait for next poll
            # Close the detail panel by pressing Escape
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(1)
            except Exception:
                pass

        # All done?
        if len(downloaded_ids) + len(permanently_failed_ids) >= expected_count:
            break

        if on_progress:
            await on_progress(
                f"已下载 {len(downloaded_ids)}/{expected_count}，{poll_interval//60}分钟后再次检查..."
            )
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.info(
        "Download finished: %d downloaded, %d permanently failed, %d expected",
        len(downloaded_ids), len(permanently_failed_ids), expected_count,
    )
    return results


async def _recover_failed_task(page: Page):
    """On the open detail panel of a failed task: click 三点 → 在聊天中查找,
    then click reply buttons until generation resumes, then return to history."""
    from config import HISTORY_URL

    # Click the 三点 (more) button — bottom-right of detail panel, button with no text
    clicked_more = await page.evaluate("""() => {
        // Find buttons in the right panel that are bottom-right and have no text
        const btns = Array.from(document.querySelectorAll('button'));
        // Filter: no text, in right half, bottom area, square-ish
        const candidates = btns.filter(b => {
            const rect = b.getBoundingClientRect();
            const txt = (b.innerText || '').trim();
            return !txt
                && rect.width > 0 && rect.height > 0
                && rect.x > window.innerWidth / 2
                && rect.y > window.innerHeight * 0.6
                && Math.abs(rect.width - rect.height) < 20;
        });
        // Sort by y descending (bottom-most first), then x descending (right-most)
        candidates.sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            return (rb.y - ra.y) || (rb.x - ra.x);
        });
        if (candidates.length > 0) {
            candidates[0].click();
            return true;
        }
        return false;
    }""")

    if not clicked_more:
        logger.warning("Could not find 三点 button on detail panel")
        await page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=30000)
        return

    await asyncio.sleep(2)

    # Click "在聊天中查找" in the popup menu
    find_chat = page.locator(':text("在聊天中查找")').last
    try:
        await find_chat.click(timeout=5000)
    except Exception as e:
        logger.warning("Could not click 在聊天中查找: %s", e)
        await page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=30000)
        return

    # Wait for navigation to chat page
    try:
        await page.wait_for_url("**/chat**", timeout=15000)
    except Exception:
        pass
    await asyncio.sleep(5)

    # Click reply buttons until generating
    try:
        await wait_until_generating(page)
        logger.info("Recovery: re-entered generating state")
    except Exception as e:
        logger.warning("Recovery wait_until_generating failed: %s", e)

    # Return to history page to continue polling
    await page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)


async def select_trend(page: Page, category: str, trend_index: int):
    """Select a category (if specified), then click the nth trend item."""
    if category:
        cat_option = page.locator(f'text="{category}"')
        if await cat_option.count() > 0:
            await cat_option.first.click()
            logger.info("Selected category: %s", category)
            await asyncio.sleep(1)
        else:
            logger.warning("Category '%s' not found, using default trends", category)

    # Click the nth trend item
    trend_items = page.locator(SELECTORS["trend_items"])
    count = await trend_items.count()
    if count == 0:
        raise RuntimeError("No trend items found on page")

    actual_index = trend_index % count
    await trend_items.nth(actual_index).click()
    logger.info("Selected trend item %d/%d", actual_index + 1, count)
    await asyncio.sleep(0.5)


async def reset_for_next_round(page: Page):
    """Start a new chat for the next round."""
    await click_new_chat(page)


async def run_scripted_steps(
    page: Page,
    input_data: InputData,
    trend_index: int,
    on_progress: OnProgress | None = None,
):
    """Execute one full round on TikTok Creative Studio.

    Flow:
    1. (If not first round) Click '+ 开启新对话'
    2. Fill prompt into input box
    3. Paste images into input box
    4. Click '+ TikTok 趋势' → 'Select a trend'
    5. Select category tab (if specified)
    6. Select nth trend item
    7. Click confirm
    8. Click send
    9. Keep clicking first reply until video is generated
    """

    async def progress(msg: str):
        if on_progress:
            await on_progress(msg)

    await progress("等待页面加载...")
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(2)

    # Step 1: Always start a fresh conversation to avoid leftover content
    # Only skip if we're on a brand new /create page with no existing content
    needs_new_chat = True
    if trend_index == 0 and "/create" in page.url:
        # Check if the editor is empty (no leftover content)
        editor = page.locator('[contenteditable="true"]')
        if await editor.count() > 0:
            content = (await editor.first.text_content() or "").strip()
            if not content:
                needs_new_chat = False

    if needs_new_chat:
        await progress("开启新对话...")
        await _retry(lambda: click_new_chat(page), "New chat")
        # Ensure editor is completely clean after new chat
        await _clear_all_images(page)

    # Step 2: Fill prompt (clears any existing text via Ctrl+A + Delete)
    await progress("填入提示词...")
    prompt = input_data.custom_prompt if input_data.custom_prompt.strip() else render_prompt(input_data)
    await _retry(lambda: fill_prompt(page, prompt), "Fill prompt")

    # Step 3: Paste images — stuck ones get removed automatically
    if input_data.image_paths:
        await progress("上传图片到输入框...")
        try:
            await paste_images(page, input_data.image_paths)
        except Exception as e:
            logger.warning("Image upload failed: %s, continuing without images", e)
            await progress("图片上传失败，继续...")
        await progress("图片处理完成")

    # Step 4: Click '+ TikTok 趋势' then 'Select a trend'
    try:
        await progress("打开趋势选择...")
        await _retry(lambda: click_tiktok_trend(page), "Click TikTok trend")
        await _retry(lambda: open_trend_selector(page), "Open trend selector")
    except Exception as e:
        logger.warning("Failed to open trend selector: %s", e)
        await progress(f"打开趋势选择失败: {e}，跳过此轮...")
        return ""

    # Step 5: Select category (if specified)
    if input_data.category:
        await progress(f"选择类目: {input_data.category}")
        await select_category_tab(page, input_data.category)

    # Step 5.5: Select sub-category (if specified)
    if input_data.sub_category:
        await progress(f"选择子分类: {input_data.sub_category}")
        await select_sub_category(page, input_data.sub_category)

    # Step 6: Select trend item
    try:
        await progress(f"选择第 {trend_index + 1} 个趋势...")
        await _retry(lambda: select_trend_item(page, trend_index), "Select trend item")
    except Exception as e:
        logger.warning("Failed to select trend item: %s", e)
        await progress(f"选择趋势失败: {e}，跳过此轮...")
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        return ""

    # Step 7: Confirm
    try:
        await progress("确认趋势选择...")
        await _retry(lambda: confirm_trend(page), "Confirm trend")
    except Exception as e:
        logger.warning("Failed to confirm trend: %s", e)
        await progress(f"确认失败: {e}，跳过此轮...")
        return ""

    # Step 8: Send
    try:
        await progress("发送生成请求...")
        await _retry(lambda: click_send(page), "Click send")
    except Exception as e:
        logger.warning("Failed to send: %s", e)
        await progress(f"发送失败: {e}，跳过此轮...")
        return ""

    await progress("等待生成，自动选择回复...")
    await progress("固定步骤完成")

    # Return the conversation title from sidebar (first item = current conversation)
    await asyncio.sleep(3)
    sidebar_items = page.locator('div.cursor-pointer.items-center.gap-4')
    title = ""
    if await sidebar_items.count() > 0:
        title = (await sidebar_items.first.text_content() or "").strip()[:60]
    logger.info("Current conversation title: %s", title)
    return title

