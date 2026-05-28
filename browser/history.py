from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
import time
from datetime import timedelta

from playwright.async_api import Page

from browser.helpers import cdp_click
from browser.chat import (
    advance_single_conversation, click_sidebar_by_title,
    has_fatal_error, count_generating_status,
)
from config import (
    DOWNLOADS_DIR, HISTORY_URL, TIKTOK_CREATIVE_URL,
    HISTORY_POLL_INTERVAL_S, HISTORY_MAX_WAIT_S,
)

logger = logging.getLogger(__name__)


async def download_and_advance_loop(
    page: Page,
    task_id: str,
    task_started_at,
    submitted_titles: list[str],
    dead_titles: list[str],
    pending_advance_titles: list[str],
    on_progress=None,
):
    """Check half-done tasks in chat, then download completed videos from history.

    Each poll round:
      1. Go to chat page, check each submitted conversation for half-done tasks
      2. Go to history page, download completed videos
      3. Wait and repeat
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    results: list[str] = []
    downloaded_ids: dict[str, str] = {}
    permanently_failed_ids: set[str] = set()
    chat_checked: set[str] = set()

    to_download = set(submitted_titles) - set(dead_titles)
    if not to_download:
        return results

    window_start_utc = (task_started_at.astimezone() - timedelta(seconds=60)).utctimetuple()
    window_start_str = "{:04d}{:02d}{:02d}{:02d}{:02d}{:02d}".format(
        window_start_utc.tm_year, window_start_utc.tm_mon, window_start_utc.tm_mday,
        window_start_utc.tm_hour, window_start_utc.tm_min, window_start_utc.tm_sec,
    )
    logger.info("download_and_advance_loop: window=%s, to_download=%d", window_start_str, len(to_download))

    elapsed = 0
    first_round = True

    while elapsed < HISTORY_MAX_WAIT_S:
        done_count = len(downloaded_ids) + len(permanently_failed_ids)
        if done_count >= len(to_download):
            break

        if on_progress:
            await on_progress(f"轮询: 已下载 {len(downloaded_ids)}/{len(to_download)}, 失败 {len(permanently_failed_ids)}")

        # Step 1: Check half-done tasks in chat
        titles_to_check = [t for t in submitted_titles if t not in chat_checked and t not in permanently_failed_ids]
        if titles_to_check and (first_round or pending_advance_titles):
            if on_progress:
                await on_progress(f"检查 {len(titles_to_check)} 个任务的聊天状态...")
            try:
                await page.goto(TIKTOK_CREATIVE_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await asyncio.sleep(5)

            for title in titles_to_check:
                ok = await click_sidebar_by_title(page, title)
                if not ok:
                    chat_checked.add(title)
                    continue

                fatal = await has_fatal_error(page)
                if fatal:
                    permanently_failed_ids.add(title)
                    logger.info("Chat task '%s' has fatal error: %s", title[:20], fatal)
                    chat_checked.add(title)
                    continue

                gen_count = await count_generating_status(page)
                if gen_count > 0:
                    logger.info("Chat task '%s' is generating, OK", title[:20])
                    chat_checked.add(title)
                    continue

                logger.info("Chat task '%s' is half-done, advancing...", title[:20])
                if on_progress:
                    await on_progress(f"推进半吊子任务: {title[:20]}...")
                outcome = await advance_single_conversation(page, budget_s=300)
                if outcome == "dead":
                    permanently_failed_ids.add(title)
                chat_checked.add(title)

            first_round = False

        # Step 2: Download from history page
        if on_progress:
            await on_progress("检查 history 页面下载...")
        try:
            await page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning("Navigate to history failed: %s", e)
        await asyncio.sleep(8)

        cards = await _extract_all_cards(page)
        logger.info("History: %d cards found", len(cards))

        for card in cards:
            if not await _click_card_open_popup(page, card):
                continue

            info = await _get_popup_status(page, window_start_str)
            cid = info.get("name", f"card_{card['index']}")

            if cid in downloaded_ids or cid in permanently_failed_ids:
                await _close_popup(page)
                continue
            if not info.get("inWindow", True):
                await _close_popup(page)
                continue

            status = info["status"]

            if status == "downloadable":
                save_path = str(DOWNLOADS_DIR / f"{cid}.mp4")
                if os.path.exists(save_path):
                    downloaded_ids[cid] = save_path
                    await _close_popup(page)
                    continue
                ok = await _download_from_popup(page, save_path)
                if ok:
                    results.append(save_path)
                    downloaded_ids[cid] = save_path
                    if on_progress:
                        await on_progress(f"已下载 {len(downloaded_ids)}/{len(to_download)}")
                await _close_popup(page)

            elif status == "failed":
                permanently_failed_ids.add(cid)
                await _close_popup(page)

            else:
                await _close_popup(page)

        if len(downloaded_ids) + len(permanently_failed_ids) >= len(to_download):
            break

        if on_progress:
            await on_progress(f"已下载 {len(downloaded_ids)}/{len(to_download)}，{HISTORY_POLL_INTERVAL_S}s后再检查...")
        await asyncio.sleep(HISTORY_POLL_INTERVAL_S)
        elapsed += HISTORY_POLL_INTERVAL_S

    logger.info("download_and_advance_loop done: %d downloaded, %d failed", len(downloaded_ids), len(permanently_failed_ids))
    return results


async def _extract_all_cards(page: Page) -> list[dict]:
    """Get all visible history cards by size."""
    for _ in range(15):
        count = await page.evaluate("""() => {
            const seen = new Set();
            let n = 0;
            for (const d of document.querySelectorAll('div')) {
                const r = d.getBoundingClientRect();
                if (r.width < 180 || r.width > 280 || r.height < 350 || r.height > 450) continue;
                if (r.x < 0) continue;
                const k = Math.round(r.x) + ',' + Math.round(r.y + window.scrollY);
                if (seen.has(k)) continue;
                seen.add(k);
                n++;
            }
            return n;
        }""")
        if count > 0:
            break
        await asyncio.sleep(2)
    logger.info("History page: %d cards found", count)
    return [{"index": i} for i in range(count)]


async def _click_card_open_popup(page: Page, card: dict) -> bool:
    """Click the nth card to open its modal popup using CDP mouse events."""
    idx = card["index"]

    if await page.locator('.byted-modal-body').count() > 0:
        await page.keyboard.press("Escape")
        await asyncio.sleep(2)

    box = await page.evaluate("""(idx) => {
        const seen = new Set();
        let n = 0;
        for (const d of document.querySelectorAll('div')) {
            const rect = d.getBoundingClientRect();
            if (rect.width < 180 || rect.width > 280 || rect.height < 350 || rect.height > 450) continue;
            if (rect.x < 0) continue;
            const key = Math.round(rect.x) + ',' + Math.round(rect.y + window.scrollY);
            if (seen.has(key)) continue;
            seen.add(key);
            if (n === idx) {
                d.scrollIntoView({block: 'center'});
                const newRect = d.getBoundingClientRect();
                return {x: newRect.x + newRect.width/2, y: newRect.y + newRect.height/2};
            }
            n++;
        }
        return null;
    }""", idx)

    if not box:
        return False

    await cdp_click(page, box["x"], box["y"])
    await asyncio.sleep(4)
    return await page.locator('.byted-modal-body').count() > 0


async def _get_popup_status(page: Page, window_start_str: str = "") -> dict:
    """Read status from the open byted-modal popup."""
    return await page.evaluate("""(windowStart) => {
        const modal = document.querySelector('.byted-modal-body');
        if (!modal) return { status: 'unknown', name: '', inWindow: false };

        const txt = (modal.innerText || '');
        let name = '';
        const nameMatch = txt.match(/Creative agent_(\\d{14})/);
        if (nameMatch) name = 'Creative agent_' + nameMatch[1];

        let status = 'unknown';
        let dlEnabled = false;
        const allEls = modal.querySelectorAll('*');
        for (const el of allEls) {
            const t = (el.innerText || '').trim();
            if (t === '下载' && (el.tagName.startsWith('KS-BUTTON') || el.tagName === 'BUTTON')) {
                const style = getComputedStyle(el);
                const disabled = el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'
                    || style.opacity < 0.5 || style.pointerEvents === 'none'
                    || el.classList.contains('disabled');
                if (!disabled) { dlEnabled = true; break; }
            }
        }

        let statusText = '';
        const statusLabels = ['已完成', '已导出', '正在生成', '失败'];
        for (const el of allEls) {
            if (el.children.length > 0) continue;
            const t = (el.innerText || '').trim();
            for (const label of statusLabels) {
                if (t === label) { statusText = t; break; }
            }
            if (statusText) break;
        }

        if (txt.includes('失败')) {
            status = 'failed';
        } else if (statusText === '正在生成') {
            status = 'generating';
        } else if (dlEnabled || statusText === '已完成' || statusText === '已导出') {
            status = 'downloadable';
        }

        let inWindow = true;
        if (windowStart && nameMatch) {
            if (nameMatch[1] < windowStart) inWindow = false;
        }

        return { status, name, inWindow };
    }""", window_start_str)


async def _download_from_popup(page: Page, save_path: str) -> bool:
    """Click the download button via CDP and wait for file to appear."""
    box = await page.evaluate("""() => {
        const modal = document.querySelector('.byted-modal-body');
        if (!modal) return null;
        const els = modal.querySelectorAll('ks-button-1-1-11');
        for (const el of els) {
            if ((el.innerText||'').trim() === '下载') {
                el.scrollIntoView({block: 'center'});
                return null;  // scroll first, re-read after
            }
        }
        return null;
    }""")
    await asyncio.sleep(1)

    box = await page.evaluate("""() => {
        const modal = document.querySelector('.byted-modal-body');
        if (!modal) return null;
        const els = modal.querySelectorAll('ks-button-1-1-11');
        for (const el of els) {
            if ((el.innerText||'').trim() === '下载') {
                const rect = el.getBoundingClientRect();
                return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
            }
        }
        return null;
    }""")

    if not box:
        logger.warning("Download button not found in modal")
        return False

    now = time.time()

    cdp_session = await page.context.new_cdp_session(page)
    await cdp_session.send("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(DOWNLOADS_DIR.resolve()),
    })
    x, y = box['x'], box['y']
    await cdp_session.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    await cdp_session.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
    logger.info("CDP click download at (%.0f, %.0f)", x, y)

    for _ in range(60):
        await asyncio.sleep(2)
        new_files = [f for f in glob.glob(str(DOWNLOADS_DIR / "*.mp4")) if os.path.getmtime(f) >= now - 2]
        if new_files:
            src = max(new_files, key=os.path.getmtime)
            await asyncio.sleep(3)
            final_size = os.path.getsize(src)
            await asyncio.sleep(1)
            if os.path.getsize(src) == final_size and final_size > 1000:
                if src != save_path and os.path.basename(src) != os.path.basename(save_path):
                    shutil.move(src, save_path)
                logger.info("Downloaded %s (%d bytes)", save_path, final_size)
                return True
    logger.warning("Download timed out")
    return False


async def _close_popup(page: Page):
    """Close the modal popup."""
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
    except Exception:
        pass


async def _select_status_filter(page: Page, status: str) -> bool:
    """Select a status filter from the third KS-SELECT dropdown on history page."""
    box = await page.evaluate("""() => {
        const selects = document.querySelectorAll('ks-select-1-1-11');
        if (selects.length >= 3) {
            const el = selects[2];
            const rect = el.getBoundingClientRect();
            return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
        }
        return null;
    }""")

    if not box:
        logger.warning("Could not find status filter dropdown")
        return False

    await cdp_click(page, box['x'], box['y'])
    await asyncio.sleep(2)

    opt_loc = page.locator(f'text="{status}"')
    for _ in range(3):
        if await opt_loc.count() > 0:
            el = await opt_loc.first.element_handle()
            if el:
                opt_box = await el.evaluate("""el => {
                    let target = el;
                    for (let i = 0; i < 5 && target; i++) {
                        const r = target.getBoundingClientRect();
                        if (r.width > 20 && r.height > 10 && r.y > 0) {
                            return {x: r.x + r.width/2, y: r.y + r.height/2};
                        }
                        target = target.parentElement || target.parentNode;
                    }
                    return null;
                }""")
                if opt_box and opt_box['y'] > 0:
                    await cdp_click(page, opt_box['x'], opt_box['y'])
                    await asyncio.sleep(3)
                    logger.info("Selected status filter: %s", status)
                    return True
        await asyncio.sleep(1)

    logger.warning("Could not click option '%s'", status)
    await page.keyboard.press("Escape")
    await asyncio.sleep(1)
    return False
