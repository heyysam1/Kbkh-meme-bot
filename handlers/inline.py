"""Inline mode: answer @bot queries with template results (Strict Zero Emoji)."""

import logging
from typing import List, Optional

from aiogram import Router, types
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultCachedPhoto,
)

import config
from database.queries import get_all_templates
from services.search_engine import hybrid_search_templates

logger = logging.getLogger("kbkh_meme_bot.inline")

router = Router(name="inline_router")


def _create_meme_url(template_id: int) -> Optional[str]:
    """Deep link that opens the bot straight into the editor for a template."""
    username = getattr(config, "BOT_USERNAME", "") or ""
    if not username:
        return None
    return f"https://t.me/{username}?start=tpl_{template_id}"


def _template_to_result(template: dict) -> Optional[InlineQueryResultCachedPhoto]:
    """Convert a local catalog template into an inline photo result."""
    file_id = template.get("file_id")
    template_id = template.get("id")
    if not file_id or template_id is None:
        return None

    title = (template.get("title") or template.get("name") or "Template").strip() or "Template"
    tags = (template.get("tags") or "").strip()
    description = (f"Tags: {tags}" if tags else "[Template]")[:100]

    buttons: List[List[InlineKeyboardButton]] = []
    deep_link = _create_meme_url(template_id)
    if deep_link:
        buttons.append([InlineKeyboardButton(text="[Create Meme]", url=deep_link)])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    return InlineQueryResultCachedPhoto(
        id=str(template_id),
        photo_file_id=file_id,
        title=title[:60],
        description=description,
        caption=f"[Template: {title}]",
        reply_markup=reply_markup,
    )


@router.inline_query()
async def handle_inline_search(inline_query: types.InlineQuery):
    """Serve template results for inline queries, local catalog only."""
    query = (inline_query.query or "").strip()
    templates: List[dict] = []
    try:
        if not query:
            # Empty query: show trending templates
            templates = await get_all_templates(limit=10)
        else:
            bundle = await hybrid_search_templates(query, limit=10)
            templates = [
                t
                for t in bundle.get("local", [])
                if t.get("file_id") and not t.get("is_external")
            ]
    except Exception as e:
        logger.warning("Inline search failed for query %r: %s", query, e)
        templates = []

    results: List[InlineQueryResultCachedPhoto] = []
    seen_ids = set()
    for template in templates:
        if template.get("id") in seen_ids:
            continue
        seen_ids.add(template.get("id"))
        result = _template_to_result(template)
        if result:
            results.append(result)
        if len(results) >= 10:
            break

    await inline_query.answer(results, cache_time=120, is_personal=True)
