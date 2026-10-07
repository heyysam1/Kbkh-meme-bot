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
)
from services.font_manager import font_manager

router = Router(name="settings_router")

class SettingsSG(StatesGroup):
    waiting_for_watermark = State()

def get_settings_keyboard(user: dict) -> InlineKeyboardMarkup:
    """Build the interactive user settings keyboard."""
    wm_enabled = bool(user.get("watermark_enabled", 0))
    wm_status_text = "🟢 সক্রিয় (চালু)" if wm_enabled else "🔴 নিষ্ক্রিয় (বন্ধ)"
    wm_toggle_text = "🔴 বন্ধ করুন" if wm_enabled else "🟢 চালু করুন"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🔄 ওয়াটারমার্ক: {wm_toggle_text}", callback_data="cb_toggle_wm"),
            ],
            [
                InlineKeyboardButton(text="📤 নতুন ওয়াটারমার্ক আপলোড", callback_data="cb_upload_wm"),
                InlineKeyboardButton(text="📍 অবস্থান পরিবর্তন", callback_data="cb_pos_wm_menu"),
            ],
            [
                InlineKeyboardButton(text="🔤 পছন্দের ফন্ট পরিবর্তন", callback_data="cb_pref_font_menu"),
            ],
            [
                InlineKeyboardButton(text="🔙 মূল মেনু", callback_data="menu_home"),
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
    font_display = font_manager.sanitize_display_name(pref_font) if pref_font != "default" else "সিস্টেম ডিফল্ট (অটো)"

    wm_enabled = "সক্রিয় (ON)" if user.get("watermark_enabled") else "নিষ্ক্রিয় (OFF)"
    has_wm_file = "সংরক্ষিত আছে" if user.get("watermark_file_id") else "যুক্ত করা হয়নি"
    wm_pos = user.get("watermark_position", "bottom_right").replace("_", " ").title()

    dashboard_text = (
        f"⚙️ <b>ব্যবহারকারী সেটিংস ড্যাশবোর্ড:</b>\n\n"
        f"👤 <b>ইউজার আইডি:</b> <code>{user_id}</code>\n"
        f"🔤 <b>পছন্দের ফন্ট:</b> {font_display}\n"
        f"🏷️ <b>কাস্টম ওয়াটারমার্ক স্ট্যাটাস:</b> {wm_enabled}\n"
        f"🖼️ <b>ওয়াটারমার্ক ফাইল:</b> {has_wm_file}\n"
        f"📍 <b>ওয়াটারমার্ক অবস্থান:</b> {wm_pos}\n\n"
        f"নিচের বাটনগুলো দিয়ে আপনার পছন্দ অনুযায়ী পরিবর্তন করুন:"
    )

    if isinstance(event, types.CallbackQuery):
        await message.edit_text(dashboard_text, reply_markup=get_settings_keyboard(user), parse_mode="HTML")
    else:
        await message.answer(dashboard_text, reply_markup=get_settings_keyboard(user), parse_mode="HTML")

@router.callback_query(F.data == "menu_home")
async def cb_menu_home(callback: types.CallbackQuery):
    """Return to main start menu."""
    from handlers.start import get_main_menu_keyboard
    await callback.answer()
    await callback.message.edit_text(
        "🤖 <b>KBKH Telegram Meme Bot</b> - মূল মেনু:\n\nনিচের অপশনগুলো থেকে বেছে নিন:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML",
    )

@router.callback_query(F.data == "cb_toggle_wm")
async def handle_toggle_watermark(callback: types.CallbackQuery):
    """Toggle user custom watermark status."""
    user = await get_user(callback.from_user.id)
    if not user or not user.get("watermark_file_id"):
        await callback.answer("⚠️ আগে একটি ওয়াটারমার্ক আপলোড করুন!", show_alert=True)
        return

    new_state = await toggle_user_watermark(callback.from_user.id)
    status_str = "চালু" if new_state else "বন্ধ"
    await callback.answer(f"ওয়াটারমার্ক {status_str} করা হয়েছে!")
    await handle_settings_command(callback)

@router.callback_query(F.data == "cb_upload_wm")
async def handle_upload_wm_prompt(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user to send a watermark image file."""
    await state.set_state(SettingsSG.waiting_for_watermark)
    await callback.answer()
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ বাতিল", callback_data="cb_cancel_settings")]]
    )
    await callback.message.edit_text(
        "📤 <b>কাস্টম ওয়াটারমার্ক আপলোড:</b>\n\n"
        "আপনার লোগো বা ওয়াটারমার্কের ইমেজটি (PNG ফরম্যাট রেকমেন্ডেড) এখানে ফটো বা ফাইল আকারে পাঠান।\n"
        "<i>স্বচ্ছ (Transparent) ব্যাকগ্রাউন্ডের ছবি দিলে সবচেয়ে সুন্দর দেখাবে।</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )

@router.callback_query(F.data == "cb_cancel_settings")
async def handle_cancel_settings(callback: types.CallbackQuery, state: FSMContext):
    """Cancel settings FSM input."""
    await state.clear()
    await callback.answer("বাতিল করা হয়েছে।")
    await handle_settings_command(callback)

@router.message(SettingsSG.waiting_for_watermark, F.photo | F.document)
async def handle_receive_watermark(message: types.Message, state: FSMContext):
    """Save uploaded watermark file_id to user profile and activate it."""
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        file_id = message.document.file_id

    if not file_id:
        await message.answer("❌ কোনো বৈধ ছবি পাওয়া যায়নি। অনুগ্রহ করে একটি ফটো পাঠান।")
        return

    await update_user_watermark(
        user_id=message.from_user.id,
        file_id=file_id,
        enabled=1,
        position="bottom_right",
    )
    await state.clear()

    await message.answer(
        "✅ <b>আপনার কাস্টম ওয়াটারমার্ক সফলভাবে যুক্ত ও সক্রিয় করা হয়েছে!</b>\n\n"
        "এখন থেকে যেকোনো মিম তৈরির সময় এটি স্বয়ংক্রিয়ভাবে ব্যবহার করা হবে।",
        parse_mode="HTML",
    )

@router.callback_query(F.data == "cb_pos_wm_menu")
async def handle_wm_position_menu(callback: types.CallbackQuery):
    """Present watermark positioning options."""
    pos_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="↘️ নিচে ডান কোণ (Default)", callback_data="cb_set_pos:bottom_right"),
                InlineKeyboardButton(text="↙️ নিচে বাম কোণ", callback_data="cb_set_pos:bottom_left"),
            ],
            [
                InlineKeyboardButton(text="↖️ উপরে বাম কোণ", callback_data="cb_set_pos:top_left"),
                InlineKeyboardButton(text="🎯 মাঝখানে (Center)", callback_data="cb_set_pos:center"),
            ],
            [
                InlineKeyboardButton(text="🔙 ফিরে যান", callback_data="menu_settings"),
            ],
        ]
    )
    await callback.message.edit_text(
        "📍 <b>মিমে ওয়াটারমার্কের অবস্থান নির্বাচন করুন:</b>",
        reply_markup=pos_kb,
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_pos:"))
async def handle_set_wm_position(callback: types.CallbackQuery):
    """Update user's chosen watermark position."""
    pos = callback.data.split(":", 1)[1]
    await set_watermark_position(callback.from_user.id, pos)
    await callback.answer("✅ ওয়াটারমার্কের অবস্থান আপডেট করা হয়েছে!")
    await handle_settings_command(callback)

@router.callback_query(F.data == "cb_pref_font_menu")
async def handle_pref_font_menu(callback: types.CallbackQuery):
    """Present default font preference choices."""
    fonts = font_manager.get_font_list()
    kb_rows = []
    row = []

    # Option to reset to auto
    kb_rows.append([InlineKeyboardButton(text="✨ সিস্টেম ডিফল্ট (অটো)", callback_data="cb_set_pref_font:default")])

    for key, display_name in fonts:
        row.append(InlineKeyboardButton(text=display_name, callback_data=f"cb_set_pref_font:{key}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    kb_rows.append([InlineKeyboardButton(text="🔙 ফিরে যান", callback_data="menu_settings")])

    await callback.message.edit_text(
        "🔤 <b>আপনার ডিফল্ট ফন্ট নির্বাচন করুন:</b>\n"
        "(প্রতিটি মিম শুরুতে এই ফন্টে রেন্ডার হবে)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_pref_font:"))
async def handle_set_pref_font(callback: types.CallbackQuery):
    """Save selected font preference."""
    font_key = callback.data.split(":", 1)[1]
    await update_user_font(callback.from_user.id, font_key)
    await callback.answer("✅ ডিফল্ট ফন্ট আপডেট করা হয়েছে!")
    await handle_settings_command(callback)
