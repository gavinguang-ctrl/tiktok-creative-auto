from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
UPLOADS_DIR = BASE_DIR / "uploads"
DATA_DIR = BASE_DIR / "data"
SUBCATEGORIES_FILE = DATA_DIR / "subcategories.json"

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"

TIKTOK_CREATIVE_URL = "https://ads.tiktok.com/creative/creativestudio/chat"
HISTORY_URL = "https://ads.tiktok.com/creative/creativestudio/create/history"

STEP_RETRY_COUNT = 3
STEP_RETRY_DELAYS = [1.0, 3.0, 5.0]

SELECTORS = {
    "new_chat": 'text="开启新对话", text="+ 开启新对话", button:has-text("开启新对话")',
    "chat_input": '[contenteditable="true"], textarea[placeholder], .chat-input',
    "tiktok_trend_btn": 'ks-button-1-1-11:has-text("TikTok"), ks-button-1-1-11:has-text("趋势")',
    "trend_items": '.trend-item, [data-testid="trend-item"], .trend-list-item',
    "send_button": 'button[aria-label*="send"], button[type="submit"]',
}

DEFAULT_CATEGORIES = [
    "家居用品",
    "美妆个护",
    "服装及配饰",
    "健康",
    "家电",
    "运动与户外活动",
    "应用",
]
