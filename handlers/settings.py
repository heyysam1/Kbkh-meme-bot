from typing import Optional

from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
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
    get_user_lang,
    set_user_lang,
)
from services.font_manager import font_manager
from services.i18n import t

router = Router(name="settings_router")

class SettingsSG(StatesGroup):
    waiting_for_watermark = State()
    waiting_for_wm_text = State()

_POS_KEYS = {
    "top_left": "settings.pos_top_left",
    "top_right": "settings.pos_top_right",
    "bottom_left": "settings.pos_bottom_left",
    "bottom_right": "settings.pos_bottom_right",
    "bottom_center": "settings.pos_bottom_center",
}

def _pos_name(position: str, lang: str) -> str:
    """Localized watermark anchor position label."""
    return t(_POS_KEYS.get(position or "", "settings.pos_bottom_right"), lang)

def get_settings_keyboard(user: dict, lang: str = "bn") -> InlineKeyboardMarkup:
    """Build the interactive user settings keyboard."""
    wm_enabled = bool(user.get("watermark_enabled", 0))
    wm_toggle_label = t("settings.wm_on", lang) if wm_enabled else t("settings.wm_off", lang)
    pos = _pos_name(user.get("watermark_position", "bottom_right"), lang)
    scale = user.get("watermark_scale", 1.0)
    opacity = int(user.get("watermark_opacity", 0.8) * 100)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("lang.toggle", lang), callback_data="cb_toggle_lang"),
            ],
            [
                InlineKeyboardButton(text=wm_toggle_label, callback_data="cb_toggle_wm"),
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.position_btn", lang).format(pos=pos),
                    callback_data="cb_pos_wm_menu",
                ),
                InlineKeyboardButton(
                    text=t("settings.scale_btn", lang).format(scale=scale),
                    callback_data="cb_scale_wm_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.opacity_btn", lang).format(pct=opacity),
                    callback_data="cb_opacity_wm_menu",
                ),
                InlineKeyboardButton(text=t("settings.wm_text", lang), callback_data="cb_text_wm_prompt"),
            ],
            [
                InlineKeyboardButton(text=t("settings.upload_wm", lang), callback_data="cb_upload_wm"),
                InlineKeyboardButton(text=t("settings.pref_font", lang), callback_data="cb_pref_font_menu"),
            ],
            [
                InlineKeyboardButton(text=t("settings.back_dashboard", lang), callback_data="menu_home"),
            ],
        ]
    )

@router.callback_query(F.data == "menu_settings")
@router.message(Command("settings"), StateFilter("*"), flags={"state": "*"})
async def handle_settings_command(event: types.Message | types.CallbackQuery, state: Optional[FSMContext] = None):
    """Present user configuration and preferences dashboard, clearing state on command."""
    if state and isinstance(event, types.Message):
        await state.clear()
    message = event if isinstance(event, types.Message) else event.message
    user_id = event.from_user.id
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    user = await upsert_user(user_id)
    lang = await get_user_lang(user_id)
    pref_font = user.get("preferred_font", "default")
    font_display = (
        font_manager.sanitize_display_name(pref_font)
        if pref_font != "default"
        else t("settings.auto_default", lang)
    )

    wm_enabled = t("settings.enabled", lang) if user.get("watermark_enabled") else t("settings.disabled", lang)
    has_wm_file = t("settings.image_loaded", lang) if user.get("watermark_file_id") else t("settings.none", lang)
    wm_text = user.get("watermark_text") or t("settings.none", lang)
    wm_pos = _pos_name(user.get("watermark_position", "bottom_right"), lang)
    wm_scale = f"{user.get('watermark_scale', 1.0)}x"
    wm_opacity = f"{int(user.get('watermark_opacity', 0.8) * 100)}%"

    dashboard_text = (
        f"<b>{t('settings.title', lang)}</b>\n\n"
        f"• {t('settings.user_id', lang)}: <code>{user_id}</code>\n"
        f"• {t('settings.default_font', lang)}: <b>{font_display}</b>\n"
        f"• {t('settings.wm_status', lang)}: <b>{wm_enabled}</b>\n"
        f"• {t('settings.wm_image', lang)}: <code>{has_wm_file}</code>\n"
        f"• {t('settings.wm_text', lang)}: <code>{wm_text}</code>\n"
        f"• {t('settings.anchor_pos', lang)}: <b>{wm_pos}</b>\n"
        f"• {t('settings.scale', lang)}: <b>{wm_scale}</b>\n"
        f"• {t('settings.opacity', lang)}: <b>{wm_opacity}</b>\n\n"
        f"{t('settings.hint', lang)}"
    )

    if isinstance(event, types.CallbackQuery):
        await message.edit_text(dashboard_text, reply_markup=get_settings_keyboard(user, lang), parse_mode="HTML")
    else:
        await message.answer(dashboard_text, reply_markup=get_settings_keyboard(user, lang), parse_mode="HTML")

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

@router.callback_query(F.data == "cb_toggle_lang")
async def handle_toggle_lang(callback: types.CallbackQuery):
    """Flip UI language between Bangla and English, then re-render settings."""
    new_lang = "en" if await get_user_lang(callback.from_user.id) == "bn" else "bn"
    await set_user_lang(callback.from_user.id, new_lang)
    await callback.answer(t("lang.changed", new_lang))
    await handle_settings_command(callback)

@router.callback_query(F.data == "cb_toggle_wm")
async def handle_toggle_watermark(callback: types.CallbackQuery):
    """Toggle user custom watermark status."""
    new_state = await toggle_user_watermark(callback.from_user.id)
    lang = await get_user_lang(callback.from_user.id)
    status_str = t("settings.wm_toggled_on", lang) if new_state else t("settings.wm_toggled_off", lang)
    await callback.answer(status_str)
    await handle_settings_command(callback)

# ------------------------------------------------------------------------------
# Watermark Image Upload
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_upload_wm")
async def handle_upload_wm_prompt(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user to send a watermark image file."""
    await state.set_state(SettingsSG.waiting_for_watermark)
    await callback.answer()
    lang = await get_user_lang(callback.from_user.id)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("common.cancel", lang), callback_data="cb_cancel_settings")]]
    )
    await callback.message.edit_text(
        f"<b>{t('settings.upload_title', lang)}</b>\n\n"
        f"{t('settings.upload_hint', lang)}",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )

@router.message(SettingsSG.waiting_for_watermark, F.photo | F.document)
async def handle_receive_watermark(message: types.Message, state: FSMContext):
    """Save uploaded watermark file_id to user profile and activate it."""
    lang = await get_user_lang(message.from_user.id)
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        mime = (message.document.mime_type or "").lower()
        if not (mime.startswith("image/") or mime in {"image/png", "image/jpeg", "image/webp"}):
            await message.answer(t("settings.err_only_images", lang))
            return
        if message.document.file_size and message.document.file_size > 5 * 1024 * 1024:
            await message.answer(t("settings.err_file_size", lang))
            return
        file_id = message.document.file_id

    if not file_id:
        await message.answer(t("settings.err_no_image", lang))
        return

    await update_user_watermark(
        user_id=message.from_user.id,
        file_id=file_id,
        enabled=1,
        position="bottom_right",
    )
    await state.clear()
    await message.answer(t("settings.success_wm_saved", lang))

# ------------------------------------------------------------------------------
# Watermark Text Prompt
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_text_wm_prompt")
async def handle_text_wm_prompt(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user to send custom watermark text."""
    await state.set_state(SettingsSG.waiting_for_wm_text)
    await callback.answer()
    lang = await get_user_lang(callback.from_user.id)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("common.cancel", lang), callback_data="cb_cancel_settings")]]
    )
    await callback.message.edit_text(
        f"<b>{t('settings.wm_text_title', lang)}</b>\n\n"
        f"{t('settings.wm_text_hint', lang)}",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )

@router.message(SettingsSG.waiting_for_wm_text, F.text)
async def handle_receive_wm_text(message: types.Message, state: FSMContext):
    """Save custom watermark text."""
    lang = await get_user_lang(message.from_user.id)
    text_val = message.text.strip()
    if text_val == "/clear":
        await update_user_watermark_settings(message.from_user.id, text="")
        await state.clear()
        await message.answer(t("settings.success_wm_cleared", lang))
        return

    await update_user_watermark_settings(message.from_user.id, text=text_val, enabled=1)
    await state.clear()
    await message.answer(t("settings.success_wm_text_set", lang).format(text=text_val))

@router.callback_query(F.data == "cb_cancel_settings")
async def handle_cancel_settings(callback: types.CallbackQuery, state: FSMContext):
    """Cancel settings FSM input."""
    await state.clear()
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer(t("settings.cancelled", lang))
    await handle_settings_command(callback)

# ------------------------------------------------------------------------------
# Watermark Positioning Matrix
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_pos_wm_menu")
async def handle_wm_position_menu(callback: types.CallbackQuery):
    """Present watermark positioning options."""
    lang = await get_user_lang(callback.from_user.id)
    pos_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=_pos_name("top_left", lang), callback_data="cb_set_pos:top_left"),
                InlineKeyboardButton(text=_pos_name("top_right", lang), callback_data="cb_set_pos:top_right"),
            ],
            [
                InlineKeyboardButton(text=_pos_name("bottom_left", lang), callback_data="cb_set_pos:bottom_left"),
                InlineKeyboardButton(text=_pos_name("bottom_right", lang), callback_data="cb_set_pos:bottom_right"),
            ],
            [
                InlineKeyboardButton(text=_pos_name("bottom_center", lang), callback_data="cb_set_pos:bottom_center"),
            ],
            [
                InlineKeyboardButton(text=t("settings.back_settings", lang), callback_data="menu_settings"),
            ],
        ]
    )
    await callback.message.edit_text(
        f"<b>{t('settings.pos_title', lang)}</b>\n\n{t('settings.pos_hint', lang)}",
        reply_markup=pos_kb,
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_pos:"))
async def handle_set_wm_position(callback: types.CallbackQuery):
    """Update user's chosen watermark position."""
    pos = callback.data.split(":", 1)[1]
    await set_watermark_position(callback.from_user.id, pos)
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer(t("settings.pos_set", lang))
    await handle_settings_command(callback)

# ------------------------------------------------------------------------------
# Watermark Scale & Opacity Menus
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_scale_wm_menu")
async def handle_wm_scale_menu(callback: types.CallbackQuery):
    """Present watermark scale multiplier options."""
    lang = await get_user_lang(callback.from_user.id)
    scale_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="0.5x", callback_data="cb_set_scale:0.5"),
                InlineKeyboardButton(
                    text=f"1.0x ({t('settings.standard', lang)})",
                    callback_data="cb_set_scale:1.0",
                ),
            ],
            [
                InlineKeyboardButton(text="1.5x", callback_data="cb_set_scale:1.5"),
                InlineKeyboardButton(
                    text=f"2.0x ({t('settings.large', lang)})",
                    callback_data="cb_set_scale:2.0",
                ),
            ],
            [
                InlineKeyboardButton(text=t("settings.back_settings", lang), callback_data="menu_settings"),
            ],
        ]
    )
    await callback.message.edit_text(
        f"<b>{t('settings.scale_title', lang)}</b>\n\n{t('settings.scale_hint', lang)}",
        reply_markup=scale_kb,
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_scale:"))
async def handle_set_scale(callback: types.CallbackQuery):
    """Update user watermark scale."""
    val = float(callback.data.split(":", 1)[1])
    await update_user_watermark_settings(callback.from_user.id, scale=val)
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer(t("settings.scale_set", lang).format(val=val))
    await handle_settings_command(callback)

@router.callback_query(F.data == "cb_opacity_wm_menu")
async def handle_wm_opacity_menu(callback: types.CallbackQuery):
    """Present watermark opacity options."""
    lang = await get_user_lang(callback.from_user.id)
    opacity_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="25%", callback_data="cb_set_opac:0.25"),
                InlineKeyboardButton(text="50%", callback_data="cb_set_opac:0.50"),
            ],
            [
                InlineKeyboardButton(text="75%", callback_data="cb_set_opac:0.75"),
                InlineKeyboardButton(
                    text=f"100% ({t('settings.solid', lang)})",
                    callback_data="cb_set_opac:1.00",
                ),
            ],
            [
                InlineKeyboardButton(text=t("settings.back_settings", lang), callback_data="menu_settings"),
            ],
        ]
    )
    await callback.message.edit_text(
        f"<b>{t('settings.opacity_title', lang)}</b>\n\n{t('settings.opacity_hint', lang)}",
        reply_markup=opacity_kb,
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_opac:"))
async def handle_set_opacity(callback: types.CallbackQuery):
    """Update user watermark opacity."""
    val = float(callback.data.split(":", 1)[1])
    await update_user_watermark_settings(callback.from_user.id, opacity=val)
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer(t("settings.opacity_set", lang).format(pct=int(val * 100)))
    await handle_settings_command(callback)

# ------------------------------------------------------------------------------
# Font Preference Selector
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_pref_font_menu")
async def handle_pref_font_menu(callback: types.CallbackQuery):
    """Present default font preference choices."""
    lang = await get_user_lang(callback.from_user.id)
    fonts = font_manager.get_font_list()
    kb_rows = []
    row = []

    # Option to reset to auto
    kb_rows.append([InlineKeyboardButton(text=t("settings.system_default", lang), callback_data="cb_set_pref_font:default")])

    for key, display_name in fonts:
        row.append(InlineKeyboardButton(text=display_name, callback_data=f"cb_set_pref_font:{key}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    kb_rows.append([InlineKeyboardButton(text=t("settings.back_settings", lang), callback_data="menu_settings")])

    await callback.message.edit_text(
        f"<b>{t('settings.font_title', lang)}</b>\n\n"
        f"{t('settings.font_hint', lang)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_pref_font:"))
async def handle_set_pref_font(callback: types.CallbackQuery):
    """Save selected font preference."""
    font_key = callback.data.split(":", 1)[1]
    await update_user_font(callback.from_user.id, font_key)
    lang = await get_user_lang(callback.from_user.id)
    if font_key == "default":
        name = t("settings.auto_default", lang)
    else:
        name = font_manager.sanitize_display_name(font_key)
    await callback.answer(t("settings.font_updated", lang).format(name=name))
    await handle_settings_command(callback)
