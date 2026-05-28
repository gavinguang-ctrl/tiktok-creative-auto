from __future__ import annotations

import asyncio
import base64
import logging

from playwright.async_api import Page

from browser.helpers import find_element, dismiss_modal
from config import IMAGE_UPLOAD_TOTAL_TIMEOUT_S

logger = logging.getLogger(__name__)


class ImageUploadFailed(Exception):
    pass


async def paste_images(page: Page, image_paths: list[str]):
    """Upload images by simulating clipboard paste. If some get stuck, remove them."""
    if not image_paths:
        return

    await dismiss_modal(page)
    input_el = await find_element(page, "chat_input")
    await input_el.click()
    await asyncio.sleep(0.5)

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

    for img_b64, mime, filename in images_data:
        await _paste_single_image(page, img_b64, mime, filename)
        await asyncio.sleep(1.5)

    # Wait for upload, then remove stuck images in one pass
    await asyncio.sleep(5)
    for check in range(5):
        if await _is_send_enabled(page):
            logger.info("All %d images uploaded (send enabled after %ds)", len(images_data), 5 + check * 2)
            return
        await asyncio.sleep(2)

    # Still not ready after ~15s — remove all stuck images at once
    thumbs = await _inspect_thumbnails(page)
    stuck = [t for t in thumbs if t["stuck"] or not t["uploaded"]]
    if stuck:
        logger.warning("Removing %d stuck/unuploaded images", len(stuck))
        for t in reversed(stuck):
            await _remove_thumbnail_by_index(page, t["index"])
            await asyncio.sleep(0.5)

    await asyncio.sleep(2)
    if await _is_send_enabled(page):
        logger.info("Send enabled after removing stuck images")
        return

    thumbs = await _inspect_thumbnails(page)
    uploaded_count = sum(1 for t in thumbs if t["uploaded"])
    if uploaded_count < 1:
        raise ImageUploadFailed(f"No images uploaded successfully after {IMAGE_UPLOAD_TOTAL_TIMEOUT_S}s")
    logger.warning("Send still disabled but %d images uploaded", uploaded_count)


async def clear_all_images(page: Page):
    """Remove all uploaded image attachments by clicking their close buttons."""
    for _ in range(20):
        close_btns = page.locator('button.absolute.right-1.top-1:has(ks-icon-close-small)')
        count = await close_btns.count()
        if count == 0:
            break
        try:
            await close_btns.first.click()
            await asyncio.sleep(0.3)
        except Exception:
            break
    await page.evaluate("""() => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) return;
        const imgNodes = editor.querySelectorAll(
            '[data-type*="image"], [contenteditable="false"]:not(.placeholder), img'
        );
        imgNodes.forEach(el => el.remove());
    }""")


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


async def _inspect_thumbnails(page: Page) -> list[dict]:
    """Inspect all image thumbnails and return their upload status."""
    return await page.evaluate("""() => {
        const closeBtns = document.querySelectorAll('button.absolute.right-1.top-1');
        const results = [];
        for (let i = 0; i < closeBtns.length; i++) {
            const btn = closeBtns[i];
            const container = btn.closest('[class*="relative"]') || btn.parentElement;
            if (!container) continue;
            const img = container.querySelector('img');
            const src = img ? img.src : '';
            const uploaded = src.startsWith('https:') || src.startsWith('http:');
            const hasSpinner = !!container.querySelector(
                '[class*="animate"], [class*="loading"], [class*="progress"], [class*="spinner"]'
            );
            const stuck = !uploaded && hasSpinner;
            results.push({ index: i, src: src.substring(0, 30), uploaded, hasSpinner, stuck });
        }
        return results;
    }""")


async def _remove_thumbnail_by_index(page: Page, index: int):
    """Remove the thumbnail at the given index (0-based)."""
    removed = await page.evaluate("""(idx) => {
        const closeBtns = document.querySelectorAll('button.absolute.right-1.top-1');
        if (idx < closeBtns.length) {
            closeBtns[idx].click();
            return true;
        }
        return false;
    }""", index)
    if removed:
        logger.info("Removed thumbnail at index %d", index)
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
