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
    editing = State()
    live_editing = State()  # Backwards compatibility alias


# Quick toggle and testing cycles (Zero Emoji)
LAYOUT_CYCLES = ["overlay", "top_banner", "bottom_banner", "breaking_news"]
COLOR_CYCLES = ["white", "yellow", "cyan", "red", "black"]
STROKE_CYCLES = [0, 2, 4, 8]
CASE_CYCLES = ["raw", "upper", "title"]
FILTER_CYCLES = ["none", "deepfry", "grayscale", "invert"]
WM_POS_CYCLES = ["bottom_right", "bottom_left", "top_left", "top_right", "bottom_center"]
WM_SCALE_CYCLES = [0.5, 1.0, 1.5, 2.0]

# Canonical lookup dictionaries for matrix callbacks
LAYOUT_MAP = {
    "overlay": "overlay",
    "top_banner": "top_banner",
    "bottom_banner": "bottom_banner",
    "breaking": "breaking_news",
    "breaking_news": "breaking_news",
}

FONT_MAP = {
    "kalpurush": "Kalpurush.ttf",
    "anek_bangla": "AnekBangla.ttf",
    "anek_extrabold": "AnekBangla-ExtraBold.ttf",
    "anek_bangla_extrabold": "AnekBangla-ExtraBold.ttf",
    "li_siliguri": "LiSiliguri.ttf",
    "siliguri": "LiSiliguri.ttf",
    "headline_bangla": "HeadlineBangla.ttf",
    "headline": "HeadlineBangla.ttf",
    "noto_sans_bengali": "NotoSansBengali.ttf",
    "noto_bengali": "NotoSansBengali.ttf",
    "impact": "Impact.ttf",
    "anton": "Anton-Regular.ttf",
    "inter": "Inter-Bold.ttf",
    "poppins": "Poppins-Bold.ttf",
    "poppins_bold": "Poppins-Bold.ttf",
}

COLOR_MAP = {
    "white": "white",
    "black": "black",
    "yellow": "yellow",
    "red": "red",
    "cyan": "cyan",
    "#ffffff": "white",
    "#000000": "black",
    "#ffe600": "yellow",
    "#ff2222": "red",
    "#00e5ff": "cyan",
}

WM_POS_MAP = {
    "tl": "top_left",
    "tr": "top_right",
    "bl": "bottom_left",
    "br": "bottom_right",
    "bc": "bottom_center",
    "top_left": "top_left",
    "top_right": "top_right",
    "bottom_left": "bottom_left",
    "bottom_right": "bottom_right",
    "bottom_center": "bottom_center",
}

WM_POS_DISPLAY = {
    "top_left": "TL",
    "top_right": "TR",
    "bottom_left": "BL",
    "bottom_right": "BR",
    "bottom_center": "BC",
    "tl": "TL",
    "tr": "TR",
    "bl": "BL",
    "br": "BR",
    "bc": "BC",
}


# ------------------------------------------------------------------------------
# Keyboard Builders (Strict Zero Emoji)
# ------------------------------------------------------------------------------

def get_editor_keyboard(data: dict) -> InlineKeyboardMarkup:
    """Build full stateful inline control dashboard for meme editor."""
    raw_var = data.get("variant", "overlay")
    variant = raw_var.replace("_", " ").upper()
    color = data.get("text_color", "white").upper()
    stroke = f"{data.get('stroke_width', 4)}px"
    fx = data.get("filter", "none").upper()
    wm_state = "ON" if data.get("watermark_enabled", True) else "OFF"
    raw_pos = data.get("watermark_pos", "bottom_right")
    wm_pos = WM_POS_DISPLAY.get(raw_pos, "BR")
    wm_scale = f"{data.get('watermark_scale', 1.0)}x"

    font_key = data.get("font_key")
    font_name = "Select"
    if font_key:
        font_name = font_manager.sanitize_display_name(font_key)[:10]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"[Layout: {variant}]", callback_data="edit:menu:layout"),
                InlineKeyboardButton(text=f"[Color: {color}]", callback_data="edit:menu:color"),
            ],
            [
                InlineKeyboardButton(text=f"[Stroke: {stroke}]", callback_data="edit:menu:stroke"),
                InlineKeyboardButton(text=f"[Filter: {fx}]", callback_data="edit:menu:fx"),
            ],
            [
                InlineKeyboardButton(text=f"[Font: {font_name}]", callback_data="edit:menu:font"),
                InlineKeyboardButton(text=f"[Watermark: {wm_state}]", callback_data="edit:wm_toggle"),
            ],
            [
                InlineKeyboardButton(text=f"[Pos: {wm_pos}]", callback_data="edit:menu:wm_pos"),
                InlineKeyboardButton(text=f"[Scale: {wm_scale}]", callback_data="edit:menu:wm_scale"),
            ],
            [
                InlineKeyboardButton(text="[Export Photo]", callback_data="edit:export:photo"),
                InlineKeyboardButton(text="[Export Lossless Doc]", callback_data="edit:export:doc"),
            ],
            [
                InlineKeyboardButton(text="[Edit Text]", callback_data="edit:change_text"),
                InlineKeyboardButton(text="[Reset]", callback_data="edit:reset"),
                InlineKeyboardButton(text="[Cancel]", callback_data="edit:cancel"),
            ],
        ]
    )


def get_layout_matrix_keyboard() -> InlineKeyboardMarkup:
    """Build Layout Selector Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[Overlay]", callback_data="edit:layout:overlay"),
                InlineKeyboardButton(text="[Top Banner]", callback_data="edit:layout:top_banner"),
            ],
            [
                InlineKeyboardButton(text="[Bottom Banner]", callback_data="edit:layout:bottom_banner"),
                InlineKeyboardButton(text="[Breaking News]", callback_data="edit:layout:breaking"),
            ],
            [
                InlineKeyboardButton(text="[<< Back to Editor]", callback_data="edit:back"),
            ],
        ]
    )


def get_font_matrix_keyboard() -> InlineKeyboardMarkup:
    """Build Font Selector Matrix for all 10 approved fonts."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[Kalpurush]", callback_data="edit:font:kalpurush"),
                InlineKeyboardButton(text="[Anek Bangla]", callback_data="edit:font:anek_bangla"),
            ],
            [
                InlineKeyboardButton(text="[Anek ExtraBold]", callback_data="edit:font:anek_extrabold"),
                InlineKeyboardButton(text="[Li Siliguri]", callback_data="edit:font:li_siliguri"),
            ],
            [
                InlineKeyboardButton(text="[Headline Bangla]", callback_data="edit:font:headline_bangla"),
                InlineKeyboardButton(text="[Noto Sans Bengali]", callback_data="edit:font:noto_sans_bengali"),
            ],
            [
                InlineKeyboardButton(text="[Impact]", callback_data="edit:font:impact"),
                InlineKeyboardButton(text="[Anton]", callback_data="edit:font:anton"),
            ],
            [
                InlineKeyboardButton(text="[Inter]", callback_data="edit:font:inter"),
                InlineKeyboardButton(text="[Poppins Bold]", callback_data="edit:font:poppins"),
            ],
            [
                InlineKeyboardButton(text="[<< Back to Editor]", callback_data="edit:back"),
            ],
        ]
    )


def get_color_matrix_keyboard() -> InlineKeyboardMarkup:
    """Build Color & Palette Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[White]", callback_data="edit:color:#FFFFFF"),
                InlineKeyboardButton(text="[Black]", callback_data="edit:color:#000000"),
            ],
            [
                InlineKeyboardButton(text="[Yellow]", callback_data="edit:color:#FFE600"),
                InlineKeyboardButton(text="[Red]", callback_data="edit:color:#FF2222"),
            ],
            [
                InlineKeyboardButton(text="[Cyan]", callback_data="edit:color:#00E5FF"),
            ],
            [
                InlineKeyboardButton(text="[<< Back to Editor]", callback_data="edit:back"),
            ],
        ]
    )


def get_stroke_matrix_keyboard() -> InlineKeyboardMarkup:
    """Build Stroke Width Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[0px (None)]", callback_data="edit:stroke:0"),
                InlineKeyboardButton(text="[2px (Thin)]", callback_data="edit:stroke:2"),
            ],
            [
                InlineKeyboardButton(text="[5px (Medium)]", callback_data="edit:stroke:5"),
                InlineKeyboardButton(text="[8px (Thick)]", callback_data="edit:stroke:8"),
            ],
            [
                InlineKeyboardButton(text="[<< Back to Editor]", callback_data="edit:back"),
            ],
        ]
    )


def get_wm_pos_matrix_keyboard() -> InlineKeyboardMarkup:
    """Build Watermark Position Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[Top-Left (TL)]", callback_data="edit:wm_pos:tl"),
                InlineKeyboardButton(text="[Top-Right (TR)]", callback_data="edit:wm_pos:tr"),
            ],
            [
                InlineKeyboardButton(text="[Bottom-Left (BL)]", callback_data="edit:wm_pos:bl"),
                InlineKeyboardButton(text="[Bottom-Right (BR)]", callback_data="edit:wm_pos:br"),
            ],
            [
                InlineKeyboardButton(text="[Bottom-Center (BC)]", callback_data="edit:wm_pos:bc"),
            ],
            [
                InlineKeyboardButton(text="[<< Back to Editor]", callback_data="edit:back"),
            ],
        ]
    )


def get_wm_scale_matrix_keyboard() -> InlineKeyboardMarkup:
    """Build Watermark Scale Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[0.5x (Small)]", callback_data="edit:wm_scale:0.5"),
                InlineKeyboardButton(text="[1.0x (Normal)]", callback_data="edit:wm_scale:1.0"),
            ],
            [
                InlineKeyboardButton(text="[1.5x (Large)]", callback_data="edit:wm_scale:1.5"),
                InlineKeyboardButton(text="[2.0x (Huge)]", callback_data="edit:wm_scale:2.0"),
            ],
            [
                InlineKeyboardButton(text="[<< Back to Editor]", callback_data="edit:back"),
            ],
        ]
    )


def get_fx_matrix_keyboard() -> InlineKeyboardMarkup:
    """Build Canvas FX Filters Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[None]", callback_data="edit:fx:none"),
                InlineKeyboardButton(text="[Deepfry]", callback_data="edit:fx:deepfry"),
            ],
            [
                InlineKeyboardButton(text="[Grayscale]", callback_data="edit:fx:grayscale"),
                InlineKeyboardButton(text="[Invert]", callback_data="edit:fx:invert"),
            ],
            [
                InlineKeyboardButton(text="[<< Back to Editor]", callback_data="edit:back"),
            ],
        ]
    )


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

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


async def _update_live_preview(callback: types.CallbackQuery, state: FSMContext, data: dict, custom_kb: Optional[InlineKeyboardMarkup] = None):
    """Re-render in-memory and update Telegram preview image without deleting the message."""
    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        try:
            await callback.answer("[Error rendering preview.]", show_alert=True)
        except Exception:
            pass
        return

    input_media = InputMediaPhoto(
        media=BufferedInputFile(rendered_buf.getvalue(), filename="meme_preview.jpg"),
        caption="[Live Preview Mode]\nUse controls below to customize layout, colors, stroke, filters, and watermark:",
    )
    kb = custom_kb or get_editor_keyboard(data)

    try:
        await callback.message.edit_media(media=input_media, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("edit_media error: %s", e)
    except Exception as e:
        logger.warning("Failed to update preview: %s", e)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


async def _show_submenu(callback: types.CallbackQuery, kb: InlineKeyboardMarkup, title: str = ""):
    """Switch keyboard to selector matrix menu without redownloading image."""
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("edit_reply_markup error: %s", e)
    except Exception as e:
        logger.warning("Failed to update menu: %s", e)
    finally:
        try:
            await callback.answer(title)
        except Exception:
            pass


# ------------------------------------------------------------------------------
# Entry into Meme Flow & Text Submission
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("btn_raw:"))
async def handle_download_raw_template(callback: types.CallbackQuery):
    """Directly dispatch raw template files without text entry."""
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
        inline_keyboard=[[InlineKeyboardButton(text="[Cancel]", callback_data="edit:cancel")]]
    )

    title = template.get("title") or template.get("name") or "Template"
    await callback.message.answer(
        f"[Meme Editor - Selected: '{title}']\n"
        f"Enter the text for your meme.\n\n"
        f"Tip: Use '|' to divide top and bottom lines for overlay memes.\n"
        f"(Example: 'TOP TEXT | BOTTOM TEXT')",
        reply_markup=cancel_kb,
    )


# ------------------------------------------------------------------------------
# Meme Caption Text Input (Strict Non-Slash Filtering)
# ------------------------------------------------------------------------------

@router.message(EditorSG.waiting_for_text, F.text, ~F.text.startswith("/"))
async def handle_meme_text_input(message: types.Message, state: FSMContext):
    """Receive caption, generate initial preview, and open full inline editing keyboard."""
    raw_text = message.text.strip()
    MAX_CAPTION_LEN = 300
    if len(raw_text) > MAX_CAPTION_LEN:
        await message.answer(f"[Error: Caption too long. Max {MAX_CAPTION_LEN} characters. Your text: {len(raw_text)}.]")
        return

    data = await state.get_data()
    data["text"] = raw_text
    await state.update_data(text=raw_text)
    await state.set_state(EditorSG.editing)

    status_msg = await message.answer("[Rendering initial preview...]")

    rendered_buf = await _render_current_draft(message.bot, data, message.from_user.id)
    try:
        await status_msg.delete()
    except Exception:
        pass

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


# Backward compatibility alias
handle_receive_editor_text = handle_meme_text_input


# ------------------------------------------------------------------------------
# Submenu Openers
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "edit:menu:layout")
async def cb_open_layout_menu(callback: types.CallbackQuery):
    """Open Layout Matrix menu."""
    await _show_submenu(callback, get_layout_matrix_keyboard(), "[Select Layout Variant]")


@router.callback_query(F.data == "edit:menu:font" or F.data == "ed_menu_font")
async def cb_open_font_menu(callback: types.CallbackQuery):
    """Open Font Selector Matrix menu."""
    await _show_submenu(callback, get_font_matrix_keyboard(), "[Select Typography Font]")


@router.callback_query(F.data == "edit:menu:color")
async def cb_open_color_menu(callback: types.CallbackQuery):
    """Open Color & Palette Matrix menu."""
    await _show_submenu(callback, get_color_matrix_keyboard(), "[Select Text Color]")


@router.callback_query(F.data == "edit:menu:stroke")
async def cb_open_stroke_menu(callback: types.CallbackQuery):
    """Open Stroke Width Matrix menu."""
    await _show_submenu(callback, get_stroke_matrix_keyboard(), "[Select Stroke Width]")


@router.callback_query(F.data == "edit:menu:wm_pos")
async def cb_open_wm_pos_menu(callback: types.CallbackQuery):
    """Open Watermark Position Matrix menu."""
    await _show_submenu(callback, get_wm_pos_matrix_keyboard(), "[Select Watermark Position]")


@router.callback_query(F.data == "edit:menu:wm_scale")
async def cb_open_wm_scale_menu(callback: types.CallbackQuery):
    """Open Watermark Scale Matrix menu."""
    await _show_submenu(callback, get_wm_scale_matrix_keyboard(), "[Select Watermark Scale]")


@router.callback_query(F.data == "edit:menu:fx")
async def cb_open_fx_menu(callback: types.CallbackQuery):
    """Open Canvas FX Filter Matrix menu."""
    await _show_submenu(callback, get_fx_matrix_keyboard(), "[Select Canvas FX Filter]")


@router.callback_query(F.data == "edit:back" or F.data == "ed_back_to_editor")
async def cb_back_to_dashboard(callback: types.CallbackQuery, state: FSMContext):
    """Return to main editor controls without re-rendering."""
    data = await state.get_data()
    await _show_submenu(callback, get_editor_keyboard(data), "[Returned to Editor]")


# ------------------------------------------------------------------------------
# Layout Matrix Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:layout:"))
async def cb_set_layout(callback: types.CallbackQuery, state: FSMContext):
    """Apply layout variant and immediately update preview."""
    layout_type = callback.data.split(":", 2)[2].strip()
    new_variant = LAYOUT_MAP.get(layout_type, layout_type)

    data = await state.get_data()
    data["variant"] = new_variant
    await state.update_data(variant=new_variant)
    await _update_live_preview(callback, state, data)


# ------------------------------------------------------------------------------
# Font Selector Matrix Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:font:") | F.data.startswith("ed_set_font:"))
async def cb_set_font(callback: types.CallbackQuery, state: FSMContext):
    """Apply typography font from curated catalog and immediately update preview."""
    parts = callback.data.split(":")
    raw_key = parts[2] if len(parts) > 2 else parts[1]
    canonical_font = FONT_MAP.get(raw_key, raw_key)

    data = await state.get_data()
    data["font_key"] = canonical_font
    await state.update_data(font_key=canonical_font)
    await _update_live_preview(callback, state, data)


# ------------------------------------------------------------------------------
# Color & Stroke Matrix Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:color:"))
async def cb_set_color(callback: types.CallbackQuery, state: FSMContext):
    """Apply text color and immediately update preview."""
    color_val = callback.data.split(":", 2)[2].strip()
    data = await state.get_data()
    data["text_color"] = color_val
    await state.update_data(text_color=color_val)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data.startswith("edit:stroke:"))
async def cb_set_stroke(callback: types.CallbackQuery, state: FSMContext):
    """Apply stroke width and immediately update preview."""
    stroke_val_str = callback.data.split(":", 2)[2].strip()
    stroke_val = int(stroke_val_str) if stroke_val_str.isdigit() else 4

    data = await state.get_data()
    data["stroke_width"] = stroke_val
    await state.update_data(stroke_width=stroke_val)
    await _update_live_preview(callback, state, data)


# ------------------------------------------------------------------------------
# Watermark Engine Matrix Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:wm_pos:"))
async def cb_set_wm_pos(callback: types.CallbackQuery, state: FSMContext):
    """Apply watermark anchor position and immediately update preview."""
    pos_code = callback.data.split(":", 2)[2].strip()
    canonical_pos = WM_POS_MAP.get(pos_code, pos_code)

    data = await state.get_data()
    data["watermark_pos"] = canonical_pos
    await state.update_data(watermark_pos=canonical_pos)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data.startswith("edit:wm_scale:"))
async def cb_set_wm_scale(callback: types.CallbackQuery, state: FSMContext):
    """Apply watermark scale multiplier and immediately update preview."""
    scale_str = callback.data.split(":", 2)[2].strip()
    try:
        scale_val = float(scale_str)
    except ValueError:
        scale_val = 1.0

    data = await state.get_data()
    data["watermark_scale"] = scale_val
    await state.update_data(watermark_scale=scale_val)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data == "edit:wm_toggle" or F.data == "ed_toggle_wm")
async def cb_toggle_wm(callback: types.CallbackQuery, state: FSMContext):
    """Toggle watermark ON/OFF and immediately update preview."""
    data = await state.get_data()
    new_wm = not data.get("watermark_enabled", True)
    data["watermark_enabled"] = new_wm
    await state.update_data(watermark_enabled=new_wm)
    await _update_live_preview(callback, state, data)


# ------------------------------------------------------------------------------
# Canvas FX Filters Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:fx:"))
async def cb_set_fx(callback: types.CallbackQuery, state: FSMContext):
    """Apply Canvas FX filter and immediately update preview."""
    filter_val = callback.data.split(":", 2)[2].strip()
    data = await state.get_data()
    data["filter"] = filter_val
    await state.update_data(filter=filter_val)
    await _update_live_preview(callback, state, data)


# ------------------------------------------------------------------------------
# Backward-Compatible Quick Cycle Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "ed_cycle_layout")
async def cb_cycle_layout(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    curr = data.get("variant", "overlay")
    idx = (LAYOUT_CYCLES.index(curr) + 1) % len(LAYOUT_CYCLES) if curr in LAYOUT_CYCLES else 0
    new_var = LAYOUT_CYCLES[idx]
    data["variant"] = new_var
    await state.update_data(variant=new_var)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data == "ed_cycle_color")
async def cb_cycle_color(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    curr = data.get("text_color", "white")
    idx = (COLOR_CYCLES.index(curr) + 1) % len(COLOR_CYCLES) if curr in COLOR_CYCLES else 0
    new_color = COLOR_CYCLES[idx]
    data["text_color"] = new_color
    await state.update_data(text_color=new_color)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data == "ed_cycle_stroke")
async def cb_cycle_stroke(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    curr = data.get("stroke_width", 4)
    idx = (STROKE_CYCLES.index(curr) + 1) % len(STROKE_CYCLES) if curr in STROKE_CYCLES else 0
    new_stroke = STROKE_CYCLES[idx]
    data["stroke_width"] = new_stroke
    await state.update_data(stroke_width=new_stroke)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data == "ed_cycle_case")
async def cb_cycle_case(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    curr = data.get("case_mode", "raw")
    idx = (CASE_CYCLES.index(curr) + 1) % len(CASE_CYCLES) if curr in CASE_CYCLES else 0
    new_case = CASE_CYCLES[idx]
    data["case_mode"] = new_case
    await state.update_data(case_mode=new_case)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data == "ed_cycle_filter")
async def cb_cycle_filter(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    curr = data.get("filter", "none")
    idx = (FILTER_CYCLES.index(curr) + 1) % len(FILTER_CYCLES) if curr in FILTER_CYCLES else 0
    new_filter = FILTER_CYCLES[idx]
    data["filter"] = new_filter
    await state.update_data(filter=new_filter)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data == "ed_cycle_wm_pos")
async def cb_cycle_wm_pos(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    curr = data.get("watermark_pos", "bottom_right")
    idx = (WM_POS_CYCLES.index(curr) + 1) % len(WM_POS_CYCLES) if curr in WM_POS_CYCLES else 0
    new_pos = WM_POS_CYCLES[idx]
    data["watermark_pos"] = new_pos
    await state.update_data(watermark_pos=new_pos)
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data == "ed_toggle_clean")
async def cb_toggle_clean(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    new_clean = not data.get("is_clean", False)
    data["is_clean"] = new_clean
    await state.update_data(is_clean=new_clean)
    await _update_live_preview(callback, state, data)


# ------------------------------------------------------------------------------
# Action Handlers (Export, Reset, Cancel, Change Text)
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "edit:export:photo" or F.data == "ed_export_photo")
async def cb_export_photo(callback: types.CallbackQuery, state: FSMContext):
    """Export finalized meme as standard photo and reset state."""
    await callback.answer("[Exporting photo...]")
    data = await state.get_data()
    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        await callback.message.answer("[Error generating final output.]")
        return

    t_id = data.get("template_id")
    if t_id:
        await increment_template_usage(t_id)

    input_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_export.jpg")
    await callback.message.answer_photo(
        photo=input_file,
        caption="[Meme exported successfully.]",
    )
    await state.clear()


@router.callback_query(F.data == "edit:export:doc" or F.data == "ed_export_doc")
async def cb_export_doc(callback: types.CallbackQuery, state: FSMContext):
    """Export finalized meme as uncompressed lossless document and reset state."""
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
    await state.clear()


@router.callback_query(F.data == "edit:reset" or F.data == "ed_reset")
async def cb_reset_editor(callback: types.CallbackQuery, state: FSMContext):
    """Revert modifications to baseline defaults."""
    data = await state.get_data()
    data.update(
        variant="overlay",
        text_color="white",
        stroke_width=4,
        case_mode="raw",
        filter="none",
        is_clean=False,
        watermark_enabled=True,
        watermark_pos="bottom_right",
        watermark_scale=1.0,
    )
    await state.update_data(
        variant="overlay",
        text_color="white",
        stroke_width=4,
        case_mode="raw",
        filter="none",
        is_clean=False,
        watermark_enabled=True,
        watermark_pos="bottom_right",
        watermark_scale=1.0,
    )
    await _update_live_preview(callback, state, data)


@router.callback_query(F.data == "edit:change_text" or F.data == "ed_change_text")
async def cb_change_text(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user to re-enter text."""
    await state.set_state(EditorSG.waiting_for_text)
    await callback.answer()
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="[Cancel]", callback_data="edit:cancel")]]
    )
    await callback.message.answer("[Send new text for this meme]:", reply_markup=cancel_kb)


@router.callback_query(F.data == "edit:cancel" or F.data == "ed_cancel")
async def cb_cancel_editor(callback: types.CallbackQuery, state: FSMContext):
    """Cancel editing session and clear state."""
    await state.clear()
    await callback.answer("[Editor closed.]")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("[Editor session closed.]")
