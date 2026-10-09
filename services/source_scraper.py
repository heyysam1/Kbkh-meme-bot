"""
Background source scraper for KBKH Meme Engine.

Periodically fetches images from admin-configured external sources
(`sources` DB table) and ingests them into the template catalog.
Zero-disk: images stream through memory and are uploaded to Telegram
via a sink chat only to obtain a file_id, then the sink message is
deleted. Strict zero emoji, bracketed text style.
"""

import asyncio
import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from aiogram import Bot, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.types import BufferedInputFile

import config
from database.queries import (
    add_template,
    get_all_sources,
    is_url_scraped,
    mark_url_scraped,
)
from handlers.admin import is_admin

logger = logging.getLogger("kbkh_meme_bot.source_scraper")

router = Router(name="source_scraper_router")

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_IMG_SRC_RE = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
_IMG_ALT_RE = re.compile(r'alt=["\']([^"\']*)["\']', re.IGNORECASE)


def _is_image_url(url: str) -> bool:
    """Check the URL path for a known image extension (query string ignored)."""
    return urlparse(url).path.lower().endswith(IMAGE_EXTENSIONS)


def _extract_image_urls(page_url: str, html: str) -> List[Tuple[str, str]]:
    """Pull (absolute_url, alt_text) pairs from <img> tags in HTML."""
    found: List[Tuple[str, str]] = []
    for tag in _IMG_TAG_RE.findall(html):
        src_match = _IMG_SRC_RE.search(tag)
        if not src_match:
            continue
        abs_url = urljoin(page_url, src_match.group(1).strip())
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        if not _is_image_url(abs_url):
            continue
        alt_match = _IMG_ALT_RE.search(tag)
        alt = alt_match.group(1).strip() if alt_match else ""
        found.append((abs_url, alt))
    # Dedupe while preserving order
    seen = set()
    unique: List[Tuple[str, str]] = []
    for item in found:
        if item[0] not in seen:
            seen.add(item[0])
            unique.append(item)
    return unique


def _resolve_sink() -> Optional[int]:
    """Sink chat for Telegram uploads: authorized channel, else first admin."""
    if config.CHANNEL_ID:
        return config.CHANNEL_ID
    if config.ADMIN_IDS:
        return config.ADMIN_IDS[0]
    return None


async def _download_bytes(
    session: aiohttp.ClientSession, url: str
) -> Optional[bytes]:
    """Download URL content capped at MAX_IMAGE_BYTES. None on any failure."""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                return None
            length = resp.headers.get("Content-Length")
            if length and int(length) > MAX_IMAGE_BYTES:
                return None
            data = await resp.read()
            if len(data) > MAX_IMAGE_BYTES or not data:
                return None
            return data
    except Exception:
        return None


async def _upload_to_telegram(
    bot: Bot, sink: int, data: bytes
) -> Optional[str]:
    """Upload bytes to the sink chat, return file_id, delete the trace message."""
    try:
        msg = await bot.send_photo(
            sink, BufferedInputFile(data, filename="scraped.jpg")
        )
        file_id = msg.photo[-1].file_id
        try:
            await bot.delete_message(sink, msg.message_id)
        except Exception:
            pass  # best-effort cleanup; the file_id is already captured
        return file_id
    except Exception as e:
        logger.warning("Scraper: Telegram upload failed: %s", e)
        return None


async def _scrape_one_source(
    bot: Bot, source: dict, max_images: int
) -> int:
    """Scrape a single source. Never raises; returns ingested count."""
    url = (source.get("url") or "").strip()
    if not url or not source.get("is_active", 1):
        return 0
    name = source.get("name") or urlparse(url).netloc or "source"
    source_id = source.get("id")

    sink = _resolve_sink()
    if sink is None:
        logger.warning(
            "Scraper: no sink configured (CHANNEL_ID/ADMIN_IDS); skipping source %s.",
            url,
        )
        return 0

    count = 0
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10), headers=BROWSER_UA
        ) as session:
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Scraper: source %s returned HTTP %s.", url, resp.status
                        )
                        return 0
                    final_url = str(resp.url)
                    content_type = resp.headers.get("Content-Type", "")
                    if "image/" in content_type:
                        candidates = [(final_url, "")]
                    else:
                        html = await resp.text(errors="ignore")
                        candidates = _extract_image_urls(final_url, html)
            except Exception as e:
                logger.warning("Scraper: fetch failed for %s: %s", url, e)
                return 0

            for img_url, alt in candidates[:max_images]:
                try:
                    if await is_url_scraped(img_url):
                        continue
                    data = await _download_bytes(session, img_url)
                    if not data:
                        continue
                    file_id = await _upload_to_telegram(bot, sink, data)
                    if not file_id:
                        continue
                    title = (alt[:80] if alt else "") or name
                    await add_template(
                        file_id=file_id,
                        title=title,
                        tags=f"source:{name}",
                        media_type="photo",
                    )
                    await mark_url_scraped(img_url, source_id)
                    count += 1
                except Exception as e:
                    logger.warning(
                        "Scraper: image %s failed: %s", img_url, e, exc_info=True
                    )
    except Exception as e:
        logger.warning("Scraper: source %s failed: %s", url, e, exc_info=True)
    return count


async def scrape_all_sources(bot: Bot, max_images_per_source: int = 5) -> int:
    """
    Fetch new images from every active source and ingest them as templates.
    Never raises. Returns the number of newly ingested templates.
    """
    try:
        sources = await get_all_sources()
    except Exception as e:
        logger.warning("Scraper: could not load sources: %s", e)
        return 0
    ingested = 0
    for source in sources:
        try:
            ingested += await _scrape_one_source(
                bot, source, max_images_per_source
            )
        except Exception as e:
            logger.warning(
                "Scraper: source %r failed: %s",
                source.get("url"),
                e,
                exc_info=True,
            )
    return ingested


async def scraper_loop(bot: Bot, interval_hours: float = 6) -> None:
    """Run scrape_all_sources forever on an interval. Never raises."""
    logger.info(
        "[SCRAPER] Background source scraper loop started (every %s h).",
        interval_hours,
    )
    while True:
        try:
            ingested = await scrape_all_sources(bot)
            logger.info(
                "[SCRAPER] Cycle complete: %d new template(s) ingested.", ingested
            )
        except Exception as e:
            logger.warning("Scraper loop iteration failed: %s", e, exc_info=True)
        await asyncio.sleep(interval_hours * 3600)


@router.message(Command("scrape"), StateFilter("*"))
async def handle_scrape_command(message: types.Message, bot: Bot):
    """Admin-only manual trigger for the source scraper."""
    if not is_admin(message.from_user.id):
        await message.answer("[Access denied: admin only.]")
        return
    await message.answer("[Scraping configured sources...]")
    try:
        ingested = await scrape_all_sources(bot)
    except Exception as e:
        logger.warning("Manual scrape failed: %s", e, exc_info=True)
        await message.answer("[Scrape failed. Check logs.]")
        return
    await message.answer(f"[Done. Ingested {ingested} new template(s).]")
