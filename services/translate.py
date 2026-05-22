"""Simple English-to-Chinese translation using Google Translate free API."""
from __future__ import annotations

import asyncio
import logging
import urllib.request
import urllib.parse
import json

logger = logging.getLogger(__name__)


def translate_batch(texts: list[str], src: str = "en", dest: str = "zh-CN") -> list[str]:
    """Translate a list of texts from English to Chinese using Google Translate."""
    results = []
    for text in texts:
        try:
            translated = _translate_single(text, src, dest)
            results.append(translated)
        except Exception as e:
            logger.warning("Translation failed for '%s': %s", text, e)
            results.append(text)
    return results


def _translate_single(text: str, src: str, dest: str) -> str:
    """Translate a single text string."""
    url = "https://translate.googleapis.com/translate_a/single"
    params = urllib.parse.urlencode({
        "client": "gtx",
        "sl": src,
        "tl": dest,
        "dt": "t",
        "q": text,
    })
    full_url = f"{url}?{params}"
    req = urllib.request.Request(full_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        # Response format: [[["translated","original",...],...],...]
        return data[0][0][0]
