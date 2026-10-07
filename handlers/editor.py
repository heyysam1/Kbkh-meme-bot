import io
import logging
from typing import Optional
from aiogram import Router, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)

from database.queries import (
    get_template_by_id,
    increment_template_usage,
    get_user,
    upsert_user,
)
from services.font_manager import font_manager
from services.renderer import render_meme

logger = logging.getLogger("kbkh_meme_bot.editor")
router = Router(name="editor_router")

class EditorSG(StatesGroup):
    waiting_for_text = State()
    live_editing = State()

# Cycle definitions for quick toggle controls
LAYOUT_CYCLES = ["overlay", "top_banner", "bottom_banner", "breaking_news"]
COLOR_CYCLES = ["white", "yellow", "cyan", "red", "black"]
STROKE_CYCLES = [0, 2, 4, 8]
CASE_CYCLES = ["raw", "upper", "title"]
FILTER_CYCLES = ["none", "deepfry", "grayscale", "invert"]
WM_POS_CYCLES = ["bottom_right", "bottom_left", "top_left", "top_right", "bottom_center"]
WM_SCALE_CYCLES = [0.5, 1.0, 1.5, 2.0]

def get_editor_keyboard(data: dict) -> InlineKeyboardMarkup:
    """Build full stateful inline control dashboard for meme editor."""
    variant = data.get("variant", "overlay").replace("_", " ").upper()
    color = data.get("text_color", "white").upper()
    stroke = f"{data.get('stroke_width', 4)}px"
    casing = data.get("case_mode", "raw").upper()
    fx = data.get("filter", "none").upper()
    wm_state = "ON" if data.get("watermark_enabled", True) else "OFF"
    wm_pos = data.get("watermark_pos", "bottom_right").replace("_", " ").upper()
    clean_state = "ON" if data.get("is_clean", False) else "OFF"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"[Layout: {variant}]", callback_data="ed_cycle_layout"),
                InlineKeyboardButton(text=f"[Color: {color}]", callback_data="ed_cycle_color"),
            ],
            [
                InlineKeyboardButton(text=f"[Stroke: {stroke}]", callback_data="ed_cycle_stroke"),
                InlineKeyboardButton(text=f"[Case: {casing}]", callback_data="ed_cycle_case"),
            ],
            [
                InlineKeyboardButton(text=f"[Filter: {fx}]", callback_data="ed_cycle_filter"),
                InlineKeyboardButton(text="[Font: Select]", callback_data="ed_menu_font"),
            ],
            [
                InlineKeyboardButton(text=f"[Watermark: {wm_state}]", callback_data="ed_toggle_wm"),
                InlineKeyboardButton(text=f"[Pos: {wm_pos}]", callback_data="ed_cycle_wm_pos"),
                InlineKeyboardButton(text=f"[Clean: {clean_state}]", callback_data="ed_toggle_clean"),
            ],
            [
                InlineKeyboardButton(text="[Apply & Export Photo]", callback_data="ed_export_photo"),
                InlineKeyboardButton(text="[Export Lossless Doc]", callback_data="ed_export_doc"),
            ],
            [
                InlineKeyboardButton(text="[Edit Text]", callback_data="ed_change_text"),
                InlineKeyboardButton(text="[Reset]", callback_data="ed_reset"),
                InlineKeyboardButton(text="[Cancel]", callback_data="ed_cancel"),
            ],
        ]
    )

async def _fetch_telegram_file_bytes(bot, file_id: str) -> Optional[bytes]:
    """Download Telegram file into memory buffer."""
    try:
        buffer = io.BytesIO()
        await bot.download(file=file_id, destination=buffer)
        return buffer.getvalue()
    except Exception:
        return None

async def _render_current_draft(bot, data: dict, user_id: int) -> Optional[io.BytesIO]:
    """Execute rendering pipeline using current FSM state parameters."""
    raw_template_bytes = data.get("template_bytes")
    if not raw_template_bytes:
        file_id = data.get("file_id")
        if file_id:
            raw_template_bytes = await _fetch_telegram_file_bytes(bot, file_id)
            data["template_bytes"] = raw_template_bytes

    if not raw_template_bytes:
        return None

    # Resolve font path
    font_key = data.get("font_key")
    text = data.get("text", "")
    script = font_manager.detect_script(text)
    font_path = font_manager.get_font_path(font_key, script=script)

    # Fetch user watermark bytes if enabled
    watermark_bytes = None
    watermark_text = None
    user = await get_user(user_id)
    if user and user.get("watermark_file_id"):
        watermark_bytes = await _fetch_telegram_file_bytes(bot, user["watermark_file_id"])
    elif user and user.get("watermark_text"):
        watermark_text = user["watermark_text"]

    return await render_meme(
        template_bytes=raw_template_bytes,
        text=text,
        font_path=font_path,
        variant=data.get("variant", "overlay"),
        is_clean=data.get("is_clean", False),
        text_color=data.get("text_color", "white"),
        stroke_width=data.get("stroke_width", 4),
        filter_name=data.get("filter", "none"),
        case_mode=data.get("case_mode", "raw"),
        watermark_bytes=watermark_bytes,
        watermark_text=watermark_text,
        watermark_pos=data.get("watermark_pos", "bottom_right"),
        watermark_scale=data.get("watermark_scale", 1.0),
        watermark_opacity=data.get("watermark_opacity", 0.8),
        watermark_enabled=data.get("watermark_enabled", True),
    )

# ------------------------------------------------------------------------------
# Entry into Meme Flow & Text Submission
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("btn_raw:"))
async def handle_download_raw_template(callback: types.CallbackQuery):
    """
    Directly dispatch raw, uncompressed template files without text entry:
    Delivers both as a compressed Telegram photo and as an uncompressed document.
    """
    template_id_str = callback.data.split(":", 1)[1]
    if not template_id_str.isdigit():
        await callback.answer("[Error: Invalid template ID.]", show_alert=True)
        return

    template = await get_template_by_id(int(template_id_str))
    if not template:
        await callback.answer("[Error: Template not found.]", show_alert=True)
        return

    file_id = template["file_id"]
    title = template.get("title") or template.get("name") or "Template"

    await callback.answer("[Sending raw template...]")

    await callback.message.bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=file_id,
        caption=f"[Raw Photo Preview: {title}]",
    )
    await callback.message.bot.send_document(
        chat_id=callback.message.chat.id,
        document=file_id,
        caption=f"[Raw Document File: {title}]",
    )

@router.callback_query(F.data.startswith("btn_create:") | F.data.startswith("cb_create_meme:"))
async def handle_initiate_meme_creation(callback: types.CallbackQuery, state: FSMContext):
    """Triggered when user clicks [Create Meme] on any catalog item."""
    template_id_str = callback.data.split(":", 1)[1]
    if not template_id_str.isdigit():
        await callback.answer("[Error: Invalid template ID.]", show_alert=True)
        return

    template = await get_template_by_id(int(template_id_str))
    if not template:
        await callback.answer("[Error: Template not found.]", show_alert=True)
        return

    await callback.answer()
    user = await upsert_user(callback.from_user.id)
    preferred_font = user.get("preferred_font")
    if preferred_font == "default" or not preferred_font:
        preferred_font = None

    await state.clear()
    await state.update_data(
        template_id=template["id"],
        file_id=template["file_id"],
        title=template.get("title") or template.get("name") or "Template",
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

    title = template.get("title") or template.get("name") or "Template"
    await callback.message.answer(
        f"[Meme Editor - Selected: '{title}']\n"
        f"Enter the text for your meme.\n\n"
        f"Tip: Use '|' to divide top and bottom lines for overlay memes.\n"
        f"(Example: 'TOP TEXT | BOTTOM TEXT')",
        reply_markup=cancel_kb,
    )

@router.message(EditorSG.waiting_for_text, F.text, ~F.text.startswith("/"))
async def handle_receive_editor_text(message: types.Message, state: FSMContext):
    """Receive initial text, generate initial preview, and open stateful live editor."""
    raw_text = message.text.strip()
    MAX_CAPTION_LEN = 300
    if len(raw_text) > MAX_CAPTION_LEN:
        await message.answer(f"[Error: Caption too long. Max {MAX_CAPTION_LEN} characters. Your text: {len(raw_text)}.]")
        return

    data = await state.get_data()
    data["text"] = raw_text
    await state.update_data(text=raw_text)
    await state.set_state(EditorSG.live_editing)

    status_msg = await message.answer("[Rendering initial preview...]")

    rendered_buf = await _render_current_draft(message.bot, data, message.from_user.id)
    await status_msg.delete()

    if not rendered_buf:
        await message.answer("[Error: Failed to render meme canvas. Please try again.]")
        return

    input_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_preview.jpg")
    kb = get_editor_keyboard(data)

    preview_msg = await message.answer_photo(
        photo=input_file,
        caption="[Live Preview Mode]\nUse controls below to customize layout, colors, stroke, filters, and watermark:",
        reply_markup=kb,
    )
    await state.update_data(preview_message_id=preview_msg.message_id)

# ------------------------------------------------------------------------------
# Stateful Live Preview Machine & Callback Updates
# ------------------------------------------------------------------------------

async def _update_live_preview(callback: types.CallbackQuery, state: FSMContext, data: dict):
    """Re-render in-memory and update Telegram preview image without deleting the message."""
    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        await callback.answer("[Error rendering preview.]", show_alert=True)
        return

    input_media = InputMediaPhoto(
        media=BufferedInputFile(rendered_buf.getvalue(), filename="meme_preview.jpg"),
        caption="[Live Preview Mode]\nUse controls below to customize layout, colors, stroke, filters, and watermark:",
    )
    kb = get_editor_keyboard(data)

    try:
        await callback.message.edit_media(media=input_media, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("edit_media error: %s", e)
    except Exception as e:
        logger.warning("Failed to update preview: %s", e)

    await callback.answer()

@router.callback_query(EditorSG.live_editing, F.data == "ed_cycle_layout")
async def cb_cycle_layout(callback: types.CallbackQuery, state: FSMContext):
    """Cycle between Layout Variants: Overlay, Top Banner, Bottom Banner, Breaking News."""
    data = await state.get_data()
    curr = data.get("variant", "overlay")
    idx = (LAYOUT_CYCLES.index(curr) + 1) % len(LAYOUT_CYCLES) if curr in LAYOUT_CYCLES else 0
    new_var = LAYOUT_CYCLES[idx]
    data["variant"] = new_var
    await state.update_data(variant=new_var)
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_cycle_color")
async def cb_cycle_color(callback: types.CallbackQuery, state: FSMContext):
    """Cycle between Text Color Presets: White, Yellow, Cyan, Red, Black."""
    data = await state.get_data()
    curr = data.get("text_color", "white")
    idx = (COLOR_CYCLES.index(curr) + 1) % len(COLOR_CYCLES) if curr in COLOR_CYCLES else 0
    new_color = COLOR_CYCLES[idx]
    data["text_color"] = new_color
    await state.update_data(text_color=new_color)
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_cycle_stroke")
async def cb_cycle_stroke(callback: types.CallbackQuery, state: FSMContext):
    """Cycle between Stroke Widths: 0px (halo), 2px, 4px, 8px."""
    data = await state.get_data()
    curr = data.get("stroke_width", 4)
    idx = (STROKE_CYCLES.index(curr) + 1) % len(STROKE_CYCLES) if curr in STROKE_CYCLES else 0
    new_stroke = STROKE_CYCLES[idx]
    data["stroke_width"] = new_stroke
    await state.update_data(stroke_width=new_stroke)
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_cycle_case")
async def cb_cycle_case(callback: types.CallbackQuery, state: FSMContext):
    """Cycle between Text Casing: Raw, Uppercase, Title Case."""
    data = await state.get_data()
    curr = data.get("case_mode", "raw")
    idx = (CASE_CYCLES.index(curr) + 1) % len(CASE_CYCLES) if curr in CASE_CYCLES else 0
    new_case = CASE_CYCLES[idx]
    data["case_mode"] = new_case
    await state.update_data(case_mode=new_case)
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_cycle_filter")
async def cb_cycle_filter(callback: types.CallbackQuery, state: FSMContext):
    """Cycle Canvas Meme Filters: None, Deepfry, Grayscale, Invert."""
    data = await state.get_data()
    curr = data.get("filter", "none")
    idx = (FILTER_CYCLES.index(curr) + 1) % len(FILTER_CYCLES) if curr in FILTER_CYCLES else 0
    new_filter = FILTER_CYCLES[idx]
    data["filter"] = new_filter
    await state.update_data(filter=new_filter)
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_toggle_wm")
async def cb_toggle_wm(callback: types.CallbackQuery, state: FSMContext):
    """Toggle custom watermark ON/OFF."""
    data = await state.get_data()
    new_wm = not data.get("watermark_enabled", True)
    data["watermark_enabled"] = new_wm
    await state.update_data(watermark_enabled=new_wm)
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_cycle_wm_pos")
async def cb_cycle_wm_pos(callback: types.CallbackQuery, state: FSMContext):
    """Cycle watermark anchor position."""
    data = await state.get_data()
    curr = data.get("watermark_pos", "bottom_right")
    idx = (WM_POS_CYCLES.index(curr) + 1) % len(WM_POS_CYCLES) if curr in WM_POS_CYCLES else 0
    new_pos = WM_POS_CYCLES[idx]
    data["watermark_pos"] = new_pos
    await state.update_data(watermark_pos=new_pos)
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_toggle_clean")
async def cb_toggle_clean(callback: types.CallbackQuery, state: FSMContext):
    """Toggle brand logo placement (clean export)."""
    data = await state.get_data()
    new_clean = not data.get("is_clean", False)
    data["is_clean"] = new_clean
    await state.update_data(is_clean=new_clean)
    await _update_live_preview(callback, state, data)

# ------------------------------------------------------------------------------
# Font Selection Submenu
# ------------------------------------------------------------------------------

@router.callback_query(EditorSG.live_editing, F.data == "ed_menu_font")
async def cb_menu_font(callback: types.CallbackQuery):
    """Open typography selection menu."""
    fonts = font_manager.get_font_list()
    buttons = []
    for font_key, font_name in fonts[:10]:
        buttons.append([InlineKeyboardButton(text=f"[{font_name}]", callback_data=f"ed_set_font:{font_key}")])
    buttons.append([InlineKeyboardButton(text="[Back to Preview]", callback_data="ed_back_to_editor")])

    await callback.message.edit_caption(
        caption="[Select Typography Font]:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()

@router.callback_query(EditorSG.live_editing, F.data.startswith("ed_set_font:"))
async def cb_set_font(callback: types.CallbackQuery, state: FSMContext):
    """Set chosen font key and return to live preview."""
    font_key = callback.data.split(":", 1)[1]
    data = await state.get_data()
    data["font_key"] = font_key
    await state.update_data(font_key=font_key)
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_back_to_editor")
async def cb_back_to_editor(callback: types.CallbackQuery, state: FSMContext):
    """Return from font menu to main editor controls."""
    data = await state.get_data()
    await _update_live_preview(callback, state, data)

# ------------------------------------------------------------------------------
# Export Actions
# ------------------------------------------------------------------------------

@router.callback_query(EditorSG.live_editing, F.data == "ed_export_photo")
async def cb_export_photo(callback: types.CallbackQuery, state: FSMContext):
    """Export finalized meme as standard photo."""
    await callback.answer("[Exporting photo...]")
    data = await state.get_data()
    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        await callback.message.answer("[Error generating final output.]")
        return

    # Increment template popularity count
    t_id = data.get("template_id")
    if t_id:
        await increment_template_usage(t_id)

    input_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_export.jpg")
    await callback.message.answer_photo(
        photo=input_file,
        caption="[Meme exported successfully.]",
    )

@router.callback_query(EditorSG.live_editing, F.data == "ed_export_doc")
async def cb_export_doc(callback: types.CallbackQuery, state: FSMContext):
    """Export finalized meme as uncompressed lossless document."""
    await callback.answer("[Exporting document...]")
    data = await state.get_data()
    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        await callback.message.answer("[Error generating document output.]")
        return

    t_id = data.get("template_id")
    if t_id:
        await increment_template_usage(t_id)

    input_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_lossless.jpg")
    await callback.message.answer_document(
        document=input_file,
        caption="[Uncompressed lossless meme document.]",
    )

@router.callback_query(EditorSG.live_editing, F.data == "ed_reset")
async def cb_reset_editor(callback: types.CallbackQuery, state: FSMContext):
    """Reset draft parameters to defaults."""
    data = await state.get_data()
    data.update(
        variant="overlay",
        text_color="white",
        stroke_width=4,
        case_mode="raw",
        filter="none",
        is_clean=False,
    )
    await state.update_data(
        variant="overlay",
        text_color="white",
        stroke_width=4,
        case_mode="raw",
        filter="none",
        is_clean=False,
    )
    await _update_live_preview(callback, state, data)

@router.callback_query(EditorSG.live_editing, F.data == "ed_change_text")
async def cb_change_text(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user to re-enter text."""
    await state.set_state(EditorSG.waiting_for_text)
    await callback.answer()
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="[Cancel]", callback_data="ed_cancel")]]
    )
    await callback.message.answer("[Send new text for this meme]:", reply_markup=cancel_kb)

@router.callback_query(F.data == "ed_cancel")
async def cb_cancel_editor(callback: types.CallbackQuery, state: FSMContext):
    """Cancel editing session and clear state."""
    await state.clear()
    await callback.answer("[Editor closed.]")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("[Editor session closed.]")
