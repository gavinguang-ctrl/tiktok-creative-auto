from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from playwright.async_api import Page

from browser.helpers import find_element, retry, dismiss_modal, OnProgress
from browser.images import paste_images, clear_all_images
from browser.trends import (
    click_tiktok_trend, open_trend_selector, select_category_tab,
    select_sub_category, select_trend_item, confirm_trend,
)
from browser.chat import click_new_chat, fill_prompt, click_send
from models.schemas import InputData
from services.template import render_prompt

logger = logging.getLogger(__name__)


async def run_scripted_steps(
    page: Page,
    input_data: InputData,
    trend_index: int,
    on_progress: OnProgress | None = None,
):
    """Execute one full round on TikTok Creative Studio.

    Flow: new chat -> fill prompt -> paste images -> select trend -> send
    Returns conversation title for tracking.
    """

    async def progress(msg: str):
        if on_progress:
            await on_progress(msg)

    await progress("等待页面加载...")
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(2)

    # Step 1: Start fresh conversation
    needs_new_chat = True
    if trend_index == 0 and "/create" in page.url:
        editor = page.locator('[contenteditable="true"]')
        if await editor.count() > 0:
            content = (await editor.first.text_content() or "").strip()
            if not content:
                needs_new_chat = False

    if needs_new_chat:
        await progress("开启新对话...")
        await retry(lambda: click_new_chat(page), "New chat")
        await clear_all_images(page)

    # Step 2: Fill prompt
    await progress("填入提示词...")
    prompt = input_data.custom_prompt if input_data.custom_prompt.strip() else render_prompt(input_data)
    await retry(lambda: fill_prompt(page, prompt), "Fill prompt")

    # Step 3: Paste images
    if input_data.image_paths:
        await progress("上传图片到输入框...")
        await paste_images(page, input_data.image_paths)
        await progress("图片处理完成")

    # Step 4: Open trend selector
    try:
        await progress("打开趋势选择...")
        await retry(lambda: click_tiktok_trend(page), "Click TikTok trend")
        await retry(lambda: open_trend_selector(page), "Open trend selector")
    except Exception as e:
        logger.warning("Failed to open trend selector: %s", e)
        await progress(f"打开趋势选择失败: {e}，跳过此轮...")
        return ""

    # Step 5: Select category
    if input_data.category:
        await progress(f"选择类目: {input_data.category}")
        await select_category_tab(page, input_data.category)

    if input_data.sub_category:
        await progress(f"选择子分类: {input_data.sub_category}")
        await select_sub_category(page, input_data.sub_category)

    # Step 6: Select trend item
    try:
        await progress(f"选择第 {trend_index + 1} 个趋势...")
        await retry(lambda: select_trend_item(page, trend_index), "Select trend item")
    except Exception as e:
        logger.warning("Failed to select trend item: %s", e)
        await progress(f"选择趋势失败: {e}，跳过此轮...")
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        return ""

    # Step 7: Confirm
    try:
        await progress("确认趋势选择...")
        await retry(lambda: confirm_trend(page), "Confirm trend")
    except Exception as e:
        logger.warning("Failed to confirm trend: %s", e)
        await progress(f"确认失败: {e}，跳过此轮...")
        return ""

    # Step 8: Send
    try:
        await progress("发送生成请求...")
        await retry(lambda: click_send(page), "Click send")
    except Exception as e:
        logger.warning("Failed to send: %s", e)
        await progress(f"发送失败: {e}，跳过此轮...")
        return ""

    await progress("固定步骤完成")
    await asyncio.sleep(3)

    # Read conversation title from sidebar
    title = ""
    try:
        sidebar_items = page.locator('div.cursor-pointer.items-center.gap-4')
        if await sidebar_items.count() > 0:
            title = (await sidebar_items.first.text_content() or "").strip()[:60]
    except Exception as e:
        logger.warning("Sidebar title lookup failed: %s", e)

    if not title:
        title = f"__sent_{datetime.now().strftime('%Y%m%d%H%M%S')}_{trend_index}"
        logger.warning("Sidebar title empty, using placeholder: %s", title)
    logger.info("Current conversation title: %s", title)
    return title
