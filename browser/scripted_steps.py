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
    await el.fill(prompt)
    logger.info("Prompt filled (%d chars)", len(prompt))


async def paste_images(page: Page, image_paths: list[str]):
    """Upload images by simulating clipboard paste. Wait for upload, retry on failure."""
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

    # Wait for uploads to complete by checking if send button becomes enabled
    # If send button is disabled/grey, images are still uploading
    await asyncio.sleep(3)
    for check in range(10):
        is_disabled = await page.evaluate("""() => {
            const sendBtn = document.querySelector('ks-icon-arrow-up-small');
            if (!sendBtn) return true;
            const btn = sendBtn.closest('ks-icon-button-1-1-11') || sendBtn.closest('button');
            if (!btn) return true;
            return btn.hasAttribute('disabled') || btn.getAttribute('aria-disabled') === 'true' ||
                   btn.classList.contains('disabled') || getComputedStyle(btn).opacity < 0.5 ||
                   getComputedStyle(btn).pointerEvents === 'none';
        }""")
        if not is_disabled:
            logger.info("All %d images uploaded (send button enabled)", len(images_data))
            return
        logger.info("Send button still disabled, images uploading... (%d/10)", check + 1)
        await asyncio.sleep(3)

    # Timeout - remove stuck images and retry
    logger.warning("Images stuck uploading, removing and retrying")
    await page.evaluate("""() => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) return;
        const imgNodes = editor.querySelectorAll('[data-type*="image"], [contenteditable="false"]:not(.placeholder)');
        imgNodes.forEach(el => el.remove());
    }""")
    await asyncio.sleep(2)

    # Retry paste
    for img_b64, mime, filename in images_data:
        await _paste_single_image(page, img_b64, mime, filename)
        await asyncio.sleep(2)

    await asyncio.sleep(5)
    logger.info("Retried pasting %d images", len(images_data))


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


async def click_tiktok_trend(page: Page):
    """Click the '+ TikTok 趋势' button to open trend panel."""
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

    # The industry dropdown is the first ks-select in the modal
    industry_select = page.locator('#inspiration-modal-container ks-select-1-1-11').first
    if await industry_select.count() == 0:
        logger.warning("Industry dropdown not found")
        return

    await industry_select.click()
    await asyncio.sleep(1)

    # Select the category from dropdown options
    option = page.locator(f':text-is("{category}")')
    if await option.count() > 0:
        await option.first.click()
        logger.info("Selected industry: %s", category)
        await asyncio.sleep(1)
    else:
        # Try partial match
        option2 = page.locator(f':text("{category}")')
        if await option2.count() > 0:
            await option2.first.click()
            logger.info("Selected industry (partial): %s", category)
            await asyncio.sleep(1)
        else:
            logger.warning("Industry '%s' not found in dropdown, pressing Escape", category)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)


async def select_trend_item(page: Page, trend_index: int):
    """Select the nth trend item (radio button) in the modal."""
    trend_radios = page.locator('#inspiration-modal-scroll-container ks-radio-1-1-11')
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


async def wait_until_generating(page: Page, timeout_s: int = 300):
    """Keep clicking first reply until '正在生成视频' appears, then return immediately."""
    elapsed = 0
    while elapsed < timeout_s:
        generating = page.locator(':text("正在生成视频")')
        if await generating.count() > 0:
            logger.info("Video generation started")
            return True

        reply_btn = page.locator('button.tiktok-bodySm.rounded-full.border')
        try:
            if await reply_btn.count() > 0 and await reply_btn.first.is_visible(timeout=2000):
                await reply_btn.first.click()
                logger.info("Clicked reply button")
                await asyncio.sleep(5)
                elapsed += 5
            else:
                await asyncio.sleep(5)
                elapsed += 5
        except Exception:
            await asyncio.sleep(5)
            elapsed += 5

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


async def download_all_videos(page: Page, task_id: str, conversation_titles: list[str], on_progress=None):
    """Poll history conversations every 10 minutes, find OUR generated videos and download them.

    Only downloads from conversations whose titles match the ones we created.
    """
    from config import DOWNLOADS_DIR
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    results = []
    downloaded_titles = set()
    total = len(conversation_titles)

    max_wait = 5400  # 90 minutes total max
    poll_interval = 600  # 10 minutes between polls
    elapsed = 0

    while elapsed < max_wait and len(downloaded_titles) < total:
        if on_progress:
            await on_progress(f"轮询下载中: {len(results)}/{total} 已完成")

        # Get sidebar conversation items
        sidebar_items = page.locator('div.cursor-pointer.items-center.gap-4')
        count = await sidebar_items.count()

        for i in range(count):
            # Get this item's title
            try:
                item_text = (await sidebar_items.nth(i).text_content() or "").strip()
            except Exception:
                continue

            # Check if this conversation matches one of ours (partial match)
            matched_title = None
            for title in conversation_titles:
                if title and title in item_text and title not in downloaded_titles:
                    matched_title = title
                    break

            if not matched_title:
                continue

            # Click this conversation
            try:
                await sidebar_items.nth(i).click()
                await asyncio.sleep(3)
            except Exception:
                continue

            # Scroll to bottom to find the video
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            # Hover/click on the video to make download button appear
            video_el = page.locator('video, [class*="video-player"], [class*="videoPlayer"], [data-testid*="video"]')
            try:
                if await video_el.count() > 0 and await video_el.last.is_visible(timeout=3000):
                    await video_el.last.hover()
                    await asyncio.sleep(1)
                    await video_el.last.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # Look for download button
            download_btn = page.locator(':text("下载"), button[aria-label*="下载"], button[aria-label*="download"], a[download]')
            try:
                if await download_btn.count() > 0 and await download_btn.first.is_visible(timeout=3000):
                    save_path = str(DOWNLOADS_DIR / f"{task_id}_{len(results)+1}.mp4")
                    try:
                        async with page.expect_download(timeout=60000) as dl_info:
                            await download_btn.first.click()
                        download = await dl_info.value
                        await download.save_as(save_path)
                        results.append(save_path)
                        downloaded_titles.add(matched_title)
                        logger.info("Downloaded video for '%s' to %s", matched_title, save_path)
                        if on_progress:
                            await on_progress(f"已下载 {len(results)}/{total} 个视频")
                    except Exception as e:
                        logger.warning("Download failed for '%s': %s", matched_title, e)
            except Exception:
                pass

        if len(downloaded_titles) >= total:
            break

        # Wait 10 minutes before next poll
        if on_progress:
            await on_progress(f"已下载 {len(results)}/{total}，10分钟后再次检查...")
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.info("Download complete: %d/%d videos", len(results), total)
    return results


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

    # Step 1: If on /chat page, click new chat. If on /create page, skip (already fresh)
    if "/chat" in page.url or trend_index > 0:
        await progress("开启新对话...")
        await _retry(lambda: click_new_chat(page), "New chat")

    # Step 2: Fill prompt
    await progress("填入提示词...")
    prompt = render_prompt(input_data)
    await _retry(lambda: fill_prompt(page, prompt), "Fill prompt")

    # Step 3: Paste images into input box (non-blocking, skip on failure)
    if input_data.image_paths:
        await progress("上传图片到输入框...")
        try:
            await asyncio.wait_for(
                paste_images(page, input_data.image_paths),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Image upload timed out after 30s, skipping")
            await progress("图片上传超时，跳过继续...")
        except Exception as e:
            logger.warning("Image upload failed: %s, skipping", e)
            await progress("图片上传失败，跳过继续...")

    # Step 4: Click '+ TikTok 趋势' then 'Select a trend'
    await progress("打开趋势选择...")
    await _retry(lambda: click_tiktok_trend(page), "Click TikTok trend")
    await _retry(lambda: open_trend_selector(page), "Open trend selector")

    # Step 5: Select category (if specified)
    if input_data.category:
        await progress(f"选择类目: {input_data.category}")
        await select_category_tab(page, input_data.category)

    # Step 6: Select trend item
    await progress(f"选择第 {trend_index + 1} 个趋势...")
    await _retry(lambda: select_trend_item(page, trend_index), "Select trend item")

    # Step 7: Confirm
    await progress("确认趋势选择...")
    await _retry(lambda: confirm_trend(page), "Confirm trend")

    # Step 8: Send
    await progress("发送生成请求...")
    await _retry(lambda: click_send(page), "Click send")

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

