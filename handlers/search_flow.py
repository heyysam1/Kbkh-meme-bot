import io
import random
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from database.queries import (
    get_all_templates,
    get_random_template,
    add_template,
    upsert_user,
)
from handlers.editor import EditorSG
from services.search_engine import (
    hybrid_search_templates,
    get_external_template,
    fetch_external_image_bytes,
    _fetch_imgflip_memes,
    _EXTERNAL_REGISTRY,
)

router = Router(name="search_flow_router")

def build_template_choice_card(template: dict) -> InlineKeyboardMarkup:
    """Construct two-way action card for a local template (Create Meme vs Download Raw)."""
    t_id = template["id"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[Create Meme]", callback_data=f"btn_create:{t_id}"),
                InlineKeyboardButton(text="[Download Raw]", callback_data=f"btn_raw:{t_id}"),
            ]
        ]
    )

def build_external_choice_card(ext_id: str) -> InlineKeyboardMarkup:
    """Construct action card for an external template (Use Template vs Download)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[Use Template]", callback_data=f"btn_ext_use:{ext_id}"),
                InlineKeyboardButton(text="[Download]", callback_data=f"btn_ext_dl:{ext_id}"),
            ]
        ]
    )

@router.callback_query(F.data == "menu_search")
async def cb_menu_search(callback: types.CallbackQuery):
    """Prompt user to type their search query."""
    await callback.answer()
    await callback.message.answer(
        "<b>[SEARCH MEME TEMPLATES]</b>\n\n"
        "Send the title, keywords, or characters of the template you are looking for.\n"
        "Examples: <code>drake</code>, <code>tony stark</code>, <code>cheems</code>, <code>distracted boyfriend</code>",
        parse_mode="HTML",
    )

@router.message(Command("random"), StateFilter("*"), flags={"state": "*"})
async def handle_random_template(message: types.Message, state: Optional[FSMContext] = None):
    """Fetch and present a random meme template, clearing any active state."""
    if state:
        await state.clear()
    template = await get_random_template()
    if template:
        title = template.get("title") or template.get("name") or "Random Template"
        caption = (
            f"<b>[RANDOM TEMPLATE: {title}]</b>\n"
            f"Tags: {template.get('tags', 'None')}"
        )
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=template["file_id"],
                caption=caption,
                reply_markup=build_template_choice_card(template),
                parse_mode="HTML",
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
        caption = (
            f"<b>[RANDOM PUBLIC TEMPLATE: {m['name']}]</b>\n"
            f"Source: Imgflip"
        )
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=m["url"],
                caption=caption,
                reply_markup=build_external_choice_card(ext_id),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.answer("[Info: No templates currently available. Upload one using /add_template]")

from aiogram.filters import Command, StateFilter

@router.message(Command("search"), StateFilter("*"), flags={"state": "*"})
async def handle_search_command(message: types.Message, state: Optional[FSMContext] = None):
    """Handle /search <keyword> command across any state and clear state."""
    if state:
        await state.clear()
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("[Usage: /search <keyword>. Example: /search drake]")
        return

    query = parts[1].strip()
    await process_search_query(message, query)

@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def handle_text_search_fallback(message: types.Message):
    """Catch general text queries strictly outside any active FSM state as search attempts."""
    query = message.text.strip()
    if len(query) < 2:
        return
    await process_search_query(message, query)

async def process_search_query(message: types.Message, query: str):
    """Run hybrid search engine (local + external fallback) and present results."""
    wait_msg = await message.answer(f"[Searching templates for: '{query}'...]")
    search_data = await hybrid_search_templates(query, limit=4)
    try:
        await wait_msg.delete()
    except Exception:
        pass

    local_results = search_data.get("local", [])
    external_results = search_data.get("external", [])

    if not local_results and not external_results:
        await message.answer(
            f"[No templates found matching '{query}'. Try another search query or use /template.]"
        )
        return

    await message.answer(f"<b>[SEARCH RESULTS FOR: '{query}']</b>", parse_mode="HTML")

    # 1. Present local catalog matches
    for candidate in local_results:
        score_pct = int(candidate.get("search_score", 0.0) * 100)
        title = candidate.get("title") or candidate.get("name") or "Template"
        caption = (
            f"<b>{title}</b>\n"
            f"• Match Score: {score_pct}%\n"
            f"• Tags: {candidate.get('tags', 'None')}"
        )

        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=candidate["file_id"],
                caption=caption,
                reply_markup=build_template_choice_card(candidate),
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(
                caption,
                reply_markup=build_template_choice_card(candidate),
                parse_mode="HTML",
            )

    # 2. Present external fallback matches (Imgflip / Reddit)
    for ext_item in external_results:
        title = ext_item.get("title") or ext_item.get("name") or "External Meme"
        source = ext_item.get("source", "Public Web")
        caption = (
            f"<b>{title}</b>\n"
            f"• Source: {source} (Public Repository)"
        )
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=ext_item["url"],
                caption=caption,
                reply_markup=build_external_choice_card(ext_item["id"]),
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(
                caption,
                reply_markup=build_external_choice_card(ext_item["id"]),
                parse_mode="HTML",
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
    ext_id = callback.data.split(":", 1)[1]
    ext_item = get_external_template(ext_id)
    if not ext_item:
        await callback.answer("[Error: Template details unavailable. Please search again.]", show_alert=True)
        return

    await callback.answer("[Preparing external template...]")
    status_msg = await callback.message.answer("[Downloading and caching online template in-memory...]")

    # Fetch image bytes in memory
    img_bytes = await fetch_external_image_bytes(ext_item["url"])
    if not img_bytes:
        await status_msg.edit_text("[Error: Failed to fetch external image file.]")
        return

    # Upload to Telegram to obtain canonical file_id
    title = ext_item.get("title") or ext_item.get("name") or "External Template"
    input_file = BufferedInputFile(img_bytes, filename="template.jpg")
    sent = await callback.message.answer_photo(
        photo=input_file,
        caption=f"[Template Ready: {title}]",
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
        inline_keyboard=[[InlineKeyboardButton(text="[Cancel]", callback_data="ed_cancel")]]
    )

    await callback.message.answer(
        f"[Meme Editor - Selected: '{title}']\n"
        f"Enter the text for your meme.\n\n"
        f"Tip: Use '|' to divide top and bottom lines for overlay memes.\n"
        f"(Example: 'TOP TEXT | BOTTOM TEXT')",
        reply_markup=cancel_kb,
    )

@router.callback_query(F.data.startswith("btn_ext_dl:"))
async def handle_external_download_template(callback: types.CallbackQuery):
    """Directly download external template bytes and deliver to user."""
    ext_id = callback.data.split(":", 1)[1]
    ext_item = get_external_template(ext_id)
    if not ext_item:
        await callback.answer("[Error: Template details unavailable.]", show_alert=True)
        return

    await callback.answer("[Downloading template...]")
    img_bytes = await fetch_external_image_bytes(ext_item["url"])
    if not img_bytes:
        await callback.message.answer("[Error: Failed to download template image.]")
        return

    title = ext_item.get("title") or ext_item.get("name") or "Template"
    file_photo = BufferedInputFile(img_bytes, filename=f"{title}.jpg")
    file_doc = BufferedInputFile(img_bytes, filename=f"{title}.jpg")

    await callback.message.answer_photo(
        photo=file_photo,
        caption=f"[Preview: {title}]",
    )
    await callback.message.answer_document(
        document=file_doc,
        caption=f"[Original File: {title}]",
    )
