from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.queries import (
    get_user,
    upsert_user,
    update_user_font,
    update_user_watermark,
    toggle_user_watermark,
    set_watermark_position,
    update_user_watermark_settings,
)
from services.font_manager import font_manager

router = Router(name="settings_router")

class SettingsSG(StatesGroup):
    waiting_for_watermark = State()
    waiting_for_wm_text = State()

def get_settings_keyboard(user: dict) -> InlineKeyboardMarkup:
    """Build the zero-emoji interactive user settings keyboard."""
    wm_enabled = bool(user.get("watermark_enabled", 0))
    wm_toggle_label = "[Watermark: ON]" if wm_enabled else "[Watermark: OFF]"
    pos = user.get("watermark_position", "bottom_right").replace("_", " ").title()
    scale = user.get("watermark_scale", 1.0)
    opacity = int(user.get("watermark_opacity", 0.8) * 100)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=wm_toggle_label, callback_data="cb_toggle_wm"),
            ],
            [
                InlineKeyboardButton(text=f"[Position: {pos}]", callback_data="cb_pos_wm_menu"),
                InlineKeyboardButton(text=f"[Scale: {scale}x]", callback_data="cb_scale_wm_menu"),
            ],
            [
                InlineKeyboardButton(text=f"[Opacity: {opacity}%]", callback_data="cb_opacity_wm_menu"),
                InlineKeyboardButton(text="[Set Watermark Text]", callback_data="cb_text_wm_prompt"),
            ],
            [
                InlineKeyboardButton(text="[Upload Watermark Image]", callback_data="cb_upload_wm"),
                InlineKeyboardButton(text="[Preferred Font]", callback_data="cb_pref_font_menu"),
            ],
            [
                InlineKeyboardButton(text="[Back to Dashboard]", callback_data="menu_home"),
            ],
        ]
    )

@router.callback_query(F.data == "menu_settings")
@router.message(Command("settings"))
async def handle_settings_command(event: types.Message | types.CallbackQuery):
    """Present user configuration and preferences dashboard."""
    message = event if isinstance(event, types.Message) else event.message
    user_id = event.from_user.id
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    user = await upsert_user(user_id)
    pref_font = user.get("preferred_font", "default")
    font_display = font_manager.sanitize_display_name(pref_font) if pref_font != "default" else "Auto / System Default"

    wm_enabled = "ENABLED" if user.get("watermark_enabled") else "DISABLED"
    has_wm_file = "Image Loaded" if user.get("watermark_file_id") else "None"
    wm_text = user.get("watermark_text") or "None"
    wm_pos = user.get("watermark_position", "bottom_right").replace("_", " ").title()
    wm_scale = f"{user.get('watermark_scale', 1.0)}x"
    wm_opacity = f"{int(user.get('watermark_opacity', 0.8) * 100)}%"

    dashboard_text = (
        f"<b>[USER CONFIGURATION &amp; PREFERENCES]</b>\n\n"
        f"• User ID: <code>{user_id}</code>\n"
        f"• Default Font: <b>{font_display}</b>\n"
        f"• Watermark Status: <b>{wm_enabled}</b>\n"
        f"• Watermark Image: <code>{has_wm_file}</code>\n"
        f"• Watermark Text: <code>{wm_text}</code>\n"
        f"• Anchor Position: <b>{wm_pos}</b>\n"
        f"• Scale Multiplier: <b>{wm_scale}</b>\n"
        f"• Opacity Level: <b>{wm_opacity}</b>\n\n"
        f"Configure your parameters using the controls below:"
    )

    if isinstance(event, types.CallbackQuery):
        await message.edit_text(dashboard_text, reply_markup=get_settings_keyboard(user), parse_mode="HTML")
    else:
        await message.answer(dashboard_text, reply_markup=get_settings_keyboard(user), parse_mode="HTML")

@router.callback_query(F.data == "menu_home")
async def cb_menu_home(callback: types.CallbackQuery):
    """Return to main dashboard."""
    from handlers.start import get_main_menu_keyboard
    await callback.answer()
    await callback.message.edit_text(
        "<b>[KBKH MEME ENGINE - DASHBOARD]</b>\n\nSelect an option below to proceed:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML",
    )

@router.callback_query(F.data == "cb_toggle_wm")
async def handle_toggle_watermark(callback: types.CallbackQuery):
    """Toggle user custom watermark status."""
    new_state = await toggle_user_watermark(callback.from_user.id)
    status_str = "ENABLED" if new_state else "DISABLED"
    await callback.answer(f"Watermark {status_str}")
    await handle_settings_command(callback)

# ------------------------------------------------------------------------------
# Watermark Image Upload
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_upload_wm")
async def handle_upload_wm_prompt(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user to send a watermark image file."""
    await state.set_state(SettingsSG.waiting_for_watermark)
    await callback.answer()
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="[Cancel]", callback_data="cb_cancel_settings")]]
    )
    await callback.message.edit_text(
        "<b>[UPLOAD WATERMARK IMAGE]</b>\n\n"
        "Send your logo or watermark image (transparent PNG recommended) as a photo or document.\n"
        "Maximum file size: 5 MB.",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )

@router.message(SettingsSG.waiting_for_watermark, F.photo | F.document)
async def handle_receive_watermark(message: types.Message, state: FSMContext):
    """Save uploaded watermark file_id to user profile and activate it."""
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        mime = (message.document.mime_type or "").lower()
        if not (mime.startswith("image/") or mime in {"image/png", "image/jpeg", "image/webp"}):
            await message.answer("[Error: Only image files (PNG, JPG, WEBP) are accepted.]")
            return
        if message.document.file_size and message.document.file_size > 5 * 1024 * 1024:
            await message.answer("[Error: File size must be under 5 MB.]")
            return
        file_id = message.document.file_id

    if not file_id:
        await message.answer("[Error: No valid image found. Please send a photo or image file.]")
        return

    await update_user_watermark(
        user_id=message.from_user.id,
        file_id=file_id,
        enabled=1,
        position="bottom_right",
    )
    await state.clear()
    await message.answer("[Success: Custom watermark image saved and enabled.]")

# ------------------------------------------------------------------------------
# Watermark Text Prompt
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_text_wm_prompt")
async def handle_text_wm_prompt(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user to send custom watermark text."""
    await state.set_state(SettingsSG.waiting_for_wm_text)
    await callback.answer()
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="[Cancel]", callback_data="cb_cancel_settings")]]
    )
    await callback.message.edit_text(
        "<b>[SET WATERMARK TEXT]</b>\n\n"
        "Send the text you want displayed as your watermark (e.g. '@MyHandle' or 'My Brand').\n"
        "Send /clear to remove watermark text.",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )

@router.message(SettingsSG.waiting_for_wm_text, F.text)
async def handle_receive_wm_text(message: types.Message, state: FSMContext):
    """Save custom watermark text."""
    text_val = message.text.strip()
    if text_val == "/clear":
        await update_user_watermark_settings(message.from_user.id, text="")
        await state.clear()
        await message.answer("[Success: Watermark text cleared.]")
        return

    await update_user_watermark_settings(message.from_user.id, text=text_val, enabled=1)
    await state.clear()
    await message.answer(f"[Success: Watermark text set to '{text_val}' and enabled.]")

@router.callback_query(F.data == "cb_cancel_settings")
async def handle_cancel_settings(callback: types.CallbackQuery, state: FSMContext):
    """Cancel settings FSM input."""
    await state.clear()
    await callback.answer("[Cancelled]")
    await handle_settings_command(callback)

# ------------------------------------------------------------------------------
# Watermark Positioning Matrix
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_pos_wm_menu")
async def handle_wm_position_menu(callback: types.CallbackQuery):
    """Present watermark positioning options."""
    pos_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[Top Left]", callback_data="cb_set_pos:top_left"),
                InlineKeyboardButton(text="[Top Right]", callback_data="cb_set_pos:top_right"),
            ],
            [
                InlineKeyboardButton(text="[Bottom Left]", callback_data="cb_set_pos:bottom_left"),
                InlineKeyboardButton(text="[Bottom Right]", callback_data="cb_set_pos:bottom_right"),
            ],
            [
                InlineKeyboardButton(text="[Bottom Center]", callback_data="cb_set_pos:bottom_center"),
            ],
            [
                InlineKeyboardButton(text="[Back to Settings]", callback_data="menu_settings"),
            ],
        ]
    )
    await callback.message.edit_text(
        "<b>[WATERMARK ANCHOR POSITION]</b>\n\nSelect desired canvas placement:",
        reply_markup=pos_kb,
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_pos:"))
async def handle_set_wm_position(callback: types.CallbackQuery):
    """Update user's chosen watermark position."""
    pos = callback.data.split(":", 1)[1]
    await set_watermark_position(callback.from_user.id, pos)
    await callback.answer(f"[Position set to {pos}]")
    await handle_settings_command(callback)

# ------------------------------------------------------------------------------
# Watermark Scale & Opacity Menus
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_scale_wm_menu")
async def handle_wm_scale_menu(callback: types.CallbackQuery):
    """Present watermark scale multiplier options."""
    scale_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[0.5x]", callback_data="cb_set_scale:0.5"),
                InlineKeyboardButton(text="[1.0x (Standard)]", callback_data="cb_set_scale:1.0"),
            ],
            [
                InlineKeyboardButton(text="[1.5x]", callback_data="cb_set_scale:1.5"),
                InlineKeyboardButton(text="[2.0x (Large)]", callback_data="cb_set_scale:2.0"),
            ],
            [
                InlineKeyboardButton(text="[Back to Settings]", callback_data="menu_settings"),
            ],
        ]
    )
    await callback.message.edit_text(
        "<b>[WATERMARK SCALE MULTIPLIER]</b>\n\nSelect scale relative to canvas diagonal:",
        reply_markup=scale_kb,
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_scale:"))
async def handle_set_scale(callback: types.CallbackQuery):
    """Update user watermark scale."""
    val = float(callback.data.split(":", 1)[1])
    await update_user_watermark_settings(callback.from_user.id, scale=val)
    await callback.answer(f"[Scale set to {val}x]")
    await handle_settings_command(callback)

@router.callback_query(F.data == "cb_opacity_wm_menu")
async def handle_wm_opacity_menu(callback: types.CallbackQuery):
    """Present watermark opacity options."""
    opacity_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[25%]", callback_data="cb_set_opac:0.25"),
                InlineKeyboardButton(text="[50%]", callback_data="cb_set_opac:0.50"),
            ],
            [
                InlineKeyboardButton(text="[75%]", callback_data="cb_set_opac:0.75"),
                InlineKeyboardButton(text="[100% (Solid)]", callback_data="cb_set_opac:1.00"),
            ],
            [
                InlineKeyboardButton(text="[Back to Settings]", callback_data="menu_settings"),
            ],
        ]
    )
    await callback.message.edit_text(
        "<b>[WATERMARK OPACITY LEVEL]</b>\n\nSelect alpha-compositing transparency:",
        reply_markup=opacity_kb,
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_opac:"))
async def handle_set_opacity(callback: types.CallbackQuery):
    """Update user watermark opacity."""
    val = float(callback.data.split(":", 1)[1])
    await update_user_watermark_settings(callback.from_user.id, opacity=val)
    await callback.answer(f"[Opacity set to {int(val * 100)}%]")
    await handle_settings_command(callback)

# ------------------------------------------------------------------------------
# Font Preference Selector
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_pref_font_menu")
async def handle_pref_font_menu(callback: types.CallbackQuery):
    """Present default font preference choices."""
    fonts = font_manager.get_font_list()
    kb_rows = []
    row = []

    # Option to reset to auto
    kb_rows.append([InlineKeyboardButton(text="[System Default (Auto)]", callback_data="cb_set_pref_font:default")])

    for key, display_name in fonts:
        row.append(InlineKeyboardButton(text=f"[{display_name}]", callback_data=f"cb_set_pref_font:{key}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    kb_rows.append([InlineKeyboardButton(text="[Back to Settings]", callback_data="menu_settings")])

    await callback.message.edit_text(
        "<b>[PREFERRED DEFAULT FONT]</b>\n\n"
        "Select your default typography for rendering new memes:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_pref_font:"))
async def handle_set_pref_font(callback: types.CallbackQuery):
    """Save selected font preference."""
    font_key = callback.data.split(":", 1)[1]
    await update_user_font(callback.from_user.id, font_key)
    await callback.answer(f"[Default font updated to {font_key}]")
    await handle_settings_command(callback)
