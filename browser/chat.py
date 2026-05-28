from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Page

from browser.helpers import find_element, dismiss_modal
from config import STUCK_DOM_IDLE_DEAD_S

logger = logging.getLogger(__name__)


async def click_new_chat(page: Page):
    """Click '+ 开启新对话' in the left sidebar."""
    el = await find_element(page, "new_chat")
    await el.click()
    await asyncio.sleep(3)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(2)
    logger.info("Clicked new chat, page: %s", page.url)


async def fill_prompt(page: Page, prompt: str):
    """Fill the prompt text into the chat input box."""
    await dismiss_modal(page)
    el = await find_element(page, "chat_input")
    await el.click()
    await asyncio.sleep(0.3)
    await page.keyboard.press("Control+A")
    await asyncio.sleep(0.2)
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.3)
    await page.keyboard.press("Control+A")
    await asyncio.sleep(0.1)
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.3)
    await page.evaluate("(text) => navigator.clipboard.writeText(text)", prompt)
    await page.keyboard.press("Control+V")
    await asyncio.sleep(0.5)
    logger.info("Prompt filled (%d chars)", len(prompt))


async def click_send(page: Page):
    """Click the send/submit button (arrow up icon)."""
    send_btn = page.locator('ks-icon-arrow-up-small')
    if await send_btn.count() > 0 and await send_btn.first.is_visible():
        await send_btn.first.click()
    else:
        send_btn2 = page.locator('ks-icon-button-1-1-11:has(ks-icon-arrow-up-small)')
        if await send_btn2.count() > 0:
            await send_btn2.first.click()
    logger.info("Clicked send button")
    await asyncio.sleep(2)


async def try_click_first_reply(page: Page) -> bool:
    """Try to click the first reply button. Returns True if clicked."""
    reply_btn = page.locator('button.tiktok-bodySm.rounded-full.border')
    try:
        if await reply_btn.count() > 0 and await reply_btn.first.is_visible(timeout=2000):
            await reply_btn.first.click()
            logger.info("Clicked reply button")
            return True
    except Exception:
        pass
    return False


async def click_sidebar_by_title(page: Page, title: str) -> bool:
    """Click a sidebar conversation item matching the given title text."""
    clicked = await page.evaluate("""(title) => {
        const items = document.querySelectorAll('div.cursor-pointer.items-center.gap-4');
        for (const item of items) {
            if ((item.innerText || '').includes(title)) {
                item.scrollIntoView({block: 'center'});
                item.click();
                return true;
            }
        }
        return false;
    }""", title)
    if clicked:
        await asyncio.sleep(3)
    return clicked


async def has_fatal_error(page: Page) -> str | None:
    """Detect fatal error banners that mean the conversation cannot continue."""
    fatal_phrases = [
        "Something went wrong. Please resend your request",
        "Something went wrong. Please resend",
        "上传的图片违反安全准则",
        "请重新发送",
        "请重新发起",
    ]
    return await page.evaluate("""(phrases) => {
        const els = document.querySelectorAll('*');
        for (const el of els) {
            if (el.children.length > 0) continue;
            const t = (el.innerText || '').trim();
            if (t.length === 0 || t.length > 200) continue;
            for (const p of phrases) {
                if (t.includes(p)) return p;
            }
        }
        return null;
    }""", fatal_phrases)


async def count_generating_status(page: Page, debug: bool = False) -> int:
    """Count occurrences of the 'generating video' status pill (CN or EN)."""
    result = await page.evaluate("""() => {
        const matches = [];
        const els = document.querySelectorAll('*');
        for (const el of els) {
            if (el.children.length > 0) continue;
            const t = (el.innerText || '').trim();
            if (t.length === 0 || t.length > 50) continue;
            const isGenerating =
                /^正在生成/.test(t) ||
                /^Generating video/i.test(t);
            if (!isGenerating) continue;
            if (el.closest('button.tiktok-bodySm.rounded-full.border')) continue;
            matches.push({ text: t, tag: el.tagName });
        }
        return matches;
    }""")
    if debug or len(result) > 0:
        logger.info("count_generating_status: %d matches: %s", len(result), result[:3])
    return len(result)


class StuckDetector:
    """Track page progress signals to detect a truly dead page."""

    def __init__(self, page: Page):
        self.page = page
        now = asyncio.get_event_loop().time()
        self.started_at = now
        self.last_dom_hash: str | None = None
        self.last_progress_at = now
        self.rejection_count = 0

    def budget_expired(self, budget_s: float) -> bool:
        return (asyncio.get_event_loop().time() - self.started_at) >= budget_s

    async def update_progress_signals(self) -> dict:
        """Inspect page, update last_progress_at if any progress signal observed."""
        page = self.page
        now = asyncio.get_event_loop().time()

        cur_hash = await page.evaluate("""() => {
            const el = document.querySelector('main, [role=main]') || document.body;
            return el.innerText.length + ':' + el.children.length;
        }""")
        dom_changed = cur_hash != self.last_dom_hash
        if dom_changed:
            self.last_dom_hash = cur_hash
            self.last_progress_at = now

        try:
            has_reply = await page.locator('button.tiktok-bodySm.rounded-full.border').count() > 0
        except Exception:
            has_reply = False
        if has_reply:
            self.last_progress_at = now

        idle_s = now - self.last_progress_at
        return {"has_reply": has_reply, "dom_changed": dom_changed, "idle_s": idle_s}

    async def is_dead(self) -> bool:
        """Page is dead if no progress for STUCK_DOM_IDLE_DEAD_S."""
        signals = await self.update_progress_signals()
        if signals["idle_s"] >= STUCK_DOM_IDLE_DEAD_S:
            logger.warning("Page dead: %.0fs without DOM change or reply button", signals["idle_s"])
            return True
        return False


async def _handle_rejection_and_retry(page: Page, rejection_count: int = 0) -> bool:
    """Check if video was rejected. If so, handle recovery. Returns True if handled."""
    rejection = page.locator(':text("违反"), :text("rejected"), :text("audit"), :text("安全规范")')
    if await rejection.count() == 0:
        return False

    logger.warning("Video rejection detected (count=%d), attempting recovery", rejection_count)

    reply_btns = page.locator('button.tiktok-bodySm.rounded-full.border')
    count = await reply_btns.count()
    if count == 0:
        return False

    if rejection_count == 0:
        if count >= 2:
            for i in range(count):
                text = (await reply_btns.nth(i).text_content() or "").lower()
                if "storyboard" in text or "refine" in text or "rewrite" in text:
                    await reply_btns.nth(i).click()
                    logger.info("First rejection: clicked refine option: %s", text[:50])
                    await asyncio.sleep(5)
                    return True
            await reply_btns.nth(1).click()
            await asyncio.sleep(5)
            return True
        else:
            await reply_btns.first.click()
            await asyncio.sleep(5)
            return True
    else:
        for i in range(count):
            text = (await reply_btns.nth(i).text_content() or "").lower()
            if "generate" in text or "video" in text or "生成" in text:
                await reply_btns.nth(i).click()
                logger.info("Subsequent rejection: clicked generate option: %s", text[:50])
                await asyncio.sleep(5)
                return True
        await reply_btns.first.click()
        await asyncio.sleep(5)
        return True


async def advance_single_conversation(page: Page, budget_s: float = 420) -> str:
    """Push a single conversation toward '正在生成视频' / 'Generating video'.

    Returns:
        'generating' — success, video generation started
        'dead' — page is stuck/errored, no recovery possible
        'needs_more_time' — budget expired but page still alive
    """
    await asyncio.sleep(2)
    baseline_count = await count_generating_status(page, debug=True)
    logger.info("advance_single_conversation: baseline=%d, budget=%ds", baseline_count, budget_s)

    detector = StuckDetector(page)
    last_log_t = 0

    while not detector.budget_expired(budget_s):
        try:
            fatal = await has_fatal_error(page)
            if fatal:
                logger.warning("advance_single_conversation: fatal error: %s", fatal)
                return "dead"

            cur_count = await count_generating_status(page)
            if cur_count > baseline_count:
                logger.info("Video generation started (pill %d -> %d)", baseline_count, cur_count)
                return "generating"
            if cur_count < baseline_count:
                baseline_count = cur_count

            if await _handle_rejection_and_retry(page, detector.rejection_count):
                detector.rejection_count += 1
                await asyncio.sleep(5)
                continue

            if await try_click_first_reply(page):
                await asyncio.sleep(5)
                continue

            if await detector.is_dead():
                logger.warning("advance_single_conversation: page is dead")
                return "dead"

            elapsed_now = asyncio.get_event_loop().time() - detector.started_at
            if elapsed_now - last_log_t >= 30:
                logger.info("advance_single_conversation: waiting, elapsed=%.0fs", elapsed_now)
                last_log_t = elapsed_now

            await asyncio.sleep(5)

        except Exception as e:
            if "context was destroyed" in str(e) or "navigation" in str(e):
                logger.warning("advance_single_conversation: page navigated")
                return "needs_more_time"
            raise

    logger.info("advance_single_conversation: budget expired")
    return "needs_more_time"
