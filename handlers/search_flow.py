import io
import random
from typing import Optional
from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from database.queries import (
    get_all_templates,
    get_random_template,
    get_user_lang,
    add_template,
    upsert_user,
)
from handlers.editor import EditorSG
from services.i18n import t
from services.search_engine import (
    hybrid_search_templates,
    get_external_template,
    fetch_external_image_bytes,
    _fetch_imgflip_memes,
    _EXTERNAL_REGISTRY,
)

router = Router(name="search_flow_router")

# log_template_use is added by a sibling agent; import defensively so the bot
# keeps working until it lands.
try:
    from database.queries import log_template_use
except ImportError:  # pragma: no cover
    log_template_use = None

def build_template_choice_card(template: dict, lang: str = "bn") -> InlineKeyboardMarkup:
    """Construct two-way action card for a local template (Create Meme vs Download Raw)."""
    t_id = template["id"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("misc.create_meme", lang), callback_data=f"btn_create:{t_id}"),
                InlineKeyboardButton(text=t("catalog.download_raw", lang), callback_data=f"btn_raw:{t_id}"),
            ]
        ]
    )

def build_external_choice_card(ext_id: str, lang: str = "bn") -> InlineKeyboardMarkup:
    """Construct action card for an external template (Use Template vs Download)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("search.use_template", lang), callback_data=f"btn_ext_use:{ext_id}"),
                InlineKeyboardButton(text=t("search.download", lang), callback_data=f"btn_ext_dl:{ext_id}"),
            ]
        ]
    )

@router.callback_query(F.data == "menu_search")
async def cb_menu_search(callback: types.CallbackQuery):
    """Prompt user to type their search query."""
    await callback.answer()
    lang = await get_user_lang(callback.from_user.id)
    await callback.message.answer(
        t("search.prompt", lang),
        parse_mode="HTML",
    )

@router.message(Command("random"), StateFilter("*"), flags={"state": "*"})
async def handle_random_template(message: types.Message, state: Optional[FSMContext] = None):
    """Fetch and present a random meme template, clearing any active state."""
    if state:
        await state.clear()
    lang = await get_user_lang(message.from_user.id)
    template = await get_random_template()
    if template:
        title = template.get("title") or template.get("name") or "Random Template"
        caption = t("catalog.random_caption", lang).format(
            title=title, tags=template.get("tags", "None")
        )
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=template["file_id"],
                caption=caption,
                reply_markup=build_template_choice_card(template, lang=lang),
            )
            return
        except Exception:
            pass

    # Fallback to external random template if local library is empty
    memes = await _fetch_imgflip_memes()
    if memes:
        m = random.choice(memes)
        ext_id = f"ext_if_{m['id']}"
        _EXTERNAL_REGISTRY[ext_id] = {
            "id": ext_id,
            "title": m["name"],
            "name": m["name"],
            "url": m["url"],
            "is_external": True,
            "source": "Imgflip",
        }
        caption = t("search.random_public_caption", lang).format(name=m["name"])
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=m["url"],
                caption=caption,
                reply_markup=build_external_choice_card(ext_id, lang=lang),
            )
            return
        except Exception:
            pass

    await message.answer(t("catalog.no_templates", lang))

@router.message(Command("search"), StateFilter("*"), flags={"state": "*"})
async def handle_search_command(message: types.Message, state: Optional[FSMContext] = None):
    """Handle /search <keyword> command across any state and clear state."""
    if state:
        await state.clear()
    lang = await get_user_lang(message.from_user.id)
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(t("search.usage", lang))
        return

    query = parts[1].strip()
    await process_search_query(message, query)

@router.message(StateFilter(None), F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def handle_text_search_fallback(message: types.Message):
    """Catch general text queries strictly outside any active FSM state as search attempts."""
    query = message.text.strip()
    if len(query) < 2:
        return
    await process_search_query(message, query)

async def process_search_query(message: types.Message, query: str):
    """Run hybrid search engine (local + external fallback) and present results."""
    lang = await get_user_lang(message.from_user.id)
    wait_msg = await message.answer(t("search.searching", lang).format(query=query))
    search_data = await hybrid_search_templates(query, limit=4)
    try:
        await wait_msg.delete()
    except Exception:
        pass

    local_results = search_data.get("local", [])
    external_results = search_data.get("external", [])

    if not local_results and not external_results:
        await message.answer(t("search.no_results", lang).format(query=query))
        return

    await message.answer(t("search.results_title", lang).format(query=query), parse_mode="HTML")

    # 1. Present local catalog matches
    for candidate in local_results:
        score_pct = int(candidate.get("search_score", 0.0) * 100)
        title = candidate.get("title") or candidate.get("name") or "Template"
        caption = t("search.local_caption", lang).format(
            title=title, score=score_pct, tags=candidate.get("tags", "None")
        )

        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=candidate["file_id"],
                caption=caption,
                reply_markup=build_template_choice_card(candidate, lang=lang),
            )
        except Exception:
            await message.answer(
                caption,
                reply_markup=build_template_choice_card(candidate, lang=lang),
            )

    # 2. Present external fallback matches (Imgflip / Reddit)
    for ext_item in external_results:
        title = ext_item.get("title") or ext_item.get("name") or "External Meme"
        source = ext_item.get("source", "Public Web")
        caption = t("search.external_caption", lang).format(title=title, source=source)
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=ext_item["url"],
                caption=caption,
                reply_markup=build_external_choice_card(ext_item["id"], lang=lang),
            )
        except Exception:
            await message.answer(
                caption,
                reply_markup=build_external_choice_card(ext_item["id"], lang=lang),
            )

# ------------------------------------------------------------------------------
# External Template Ingestion & Direct Action Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("btn_ext_use:"))
async def handle_external_use_template(callback: types.CallbackQuery, state: FSMContext):
    """
    Ingest external image into Telegram and start FSM meme creation flow.
    Ensures zero disk storage via in-memory streaming.
    """
    lang = await get_user_lang(callback.from_user.id)
    ext_id = callback.data.split(":", 1)[1]
    ext_item = get_external_template(ext_id)
    if not ext_item:
        await callback.answer(t("search.err_unavailable", lang), show_alert=True)
        return

    await callback.answer(t("search.preparing", lang))
    status_msg = await callback.message.answer(t("search.downloading", lang))

    # Fetch image bytes in memory
    img_bytes = await fetch_external_image_bytes(ext_item["url"])
    if not img_bytes:
        await status_msg.edit_text(t("search.err_fetch", lang))
        return

    # Upload to Telegram to obtain canonical file_id
    title = ext_item.get("title") or ext_item.get("name") or "External Template"
    input_file = BufferedInputFile(img_bytes, filename="template.jpg")
    sent = await callback.message.answer_photo(
        photo=input_file,
        caption=t("search.template_ready", lang).format(title=title),
    )
    file_id = sent.photo[-1].file_id
    file_unique_id = sent.photo[-1].file_unique_id
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Save to local SQLite database catalog for zero-disk permanent caching
    template_id = await add_template(
        file_id=file_id,
        file_unique_id=file_unique_id,
        media_type="photo",
        title=title,
        name=title,
        tags=f"external, {ext_item.get('source', '')}",
        source_channel_id="external_api",
        source_channel_title=ext_item.get("source", "External"),
        added_by=callback.from_user.id,
    )

    if log_template_use is not None and template_id:
        try:
            await log_template_use(callback.from_user.id, template_id)
        except Exception:
            pass

    # Initialize EditorSG State
    user = await upsert_user(callback.from_user.id)
    preferred_font = user.get("preferred_font")
    if preferred_font == "default" or not preferred_font:
        preferred_font = None

    await state.clear()
    await state.update_data(
        template_id=template_id,
        file_id=file_id,
        title=title,
        font_key=preferred_font,
        variant="overlay",
        text_color="white",
        stroke_width=4,
        case_mode="raw",
        filter="none",
        watermark_enabled=bool(user.get("watermark_enabled", 1)),
        watermark_pos=user.get("watermark_position", "bottom_right"),
        watermark_scale=user.get("watermark_scale", 1.0),
        watermark_opacity=user.get("watermark_opacity", 0.8),
        is_clean=False,
    )
    await state.set_state(EditorSG.waiting_for_text)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("misc.cancel", lang), callback_data="ed_cancel")]]
    )

    await callback.message.answer(
        t("start.editor_prompt", lang).format(title=title),
        reply_markup=cancel_kb,
    )

@router.callback_query(F.data.startswith("btn_ext_dl:"))
async def handle_external_download_template(callback: types.CallbackQuery):
    """Directly download external template bytes and deliver to user."""
    lang = await get_user_lang(callback.from_user.id)
    ext_id = callback.data.split(":", 1)[1]
    ext_item = get_external_template(ext_id)
    if not ext_item:
        await callback.answer(t("search.err_unavailable", lang), show_alert=True)
        return

    await callback.answer(t("search.downloading", lang))
    img_bytes = await fetch_external_image_bytes(ext_item["url"])
    if not img_bytes:
        await callback.message.answer(t("search.err_download", lang))
        return

    title = ext_item.get("title") or ext_item.get("name") or "Template"
    file_photo = BufferedInputFile(img_bytes, filename=f"{title}.jpg")
    file_doc = BufferedInputFile(img_bytes, filename=f"{title}.jpg")

    await callback.message.answer_photo(
        photo=file_photo,
        caption=t("search.preview_caption", lang).format(title=title),
    )
    await callback.message.answer_document(
        document=file_doc,
        caption=t("search.original_caption", lang).format(title=title),
    )
