import io
from typing import Optional
from aiogram import Router, types, F
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
    get_all_banners,
    get_banner_by_id,
)
from services.font_manager import font_manager
from services.renderer import render_meme

router = Router(name="meme_flow_router")

class MemeFlowSG(StatesGroup):
    waiting_for_text = State()

def get_meme_actions_keyboard(has_banner: bool = False) -> InlineKeyboardMarkup:
    """Action buttons attached beneath every generated meme."""
    banner_btn = (
        InlineKeyboardButton(text="✖️ ব্যানার সরান / Remove Banner", callback_data="cb_remove_banner")
        if has_banner
        else InlineKeyboardButton(text="➕ ব্যানার যুক্ত করুন / Add Banner", callback_data="cb_banner_menu")
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Download Clean (No Logo)", callback_data="cb_clean"),
                InlineKeyboardButton(text="🎨 Change Style", callback_data="cb_style_menu"),
            ],
            [
                InlineKeyboardButton(text="🔤 Change Font", callback_data="cb_font_menu"),
                banner_btn,
            ],
        ]
    )

async def _fetch_telegram_file_bytes(bot, file_id: str) -> Optional[bytes]:
    """Helper to download a file from Telegram directly into an in-memory byte buffer."""
    try:
        buffer = io.BytesIO()
        await bot.download(file=file_id, destination=buffer)
        return buffer.getvalue()
    except Exception:
        return None

# ------------------------------------------------------------------------------
# Two-Way Template Selection Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("btn_raw:"))
async def handle_download_raw_template(callback: types.CallbackQuery):
    """
    Directly dispatch raw, uncompressed template files without text entry:
    Delivers both as a compressed Telegram photo and as an uncompressed document.
    """
    template_id_str = callback.data.split(":", 1)[1]
    if not template_id_str.isdigit():
        await callback.answer("ত্রুটি: অবৈধ টেমপ্লেট আইডি।", show_alert=True)
        return

    template = await get_template_by_id(int(template_id_str))
    if not template:
        await callback.answer("টেমপ্লেটটি খুঁজে পাওয়া যায়নি।", show_alert=True)
        return

    file_id = template["file_id"]
    name = template["name"]

    await callback.answer("📥 টেমপ্লেট পাঠানো হচ্ছে...")

    # 1. Send as Telegram photo
    await callback.message.bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=file_id,
        caption=f"📷 <b>{name}</b> (Raw Photo Preview)",
        parse_mode="HTML",
    )

    # 2. Send as uncompressed Telegram document
    await callback.message.bot.send_document(
        chat_id=callback.message.chat.id,
        document=file_id,
        caption=f"📁 <b>{name}</b> (Original Raw File)",
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("btn_create:"))
async def handle_start_meme_creation(callback: types.CallbackQuery, state: FSMContext):
    """Initiate FSM meme generation for the chosen template."""
    template_id_str = callback.data.split(":", 1)[1]
    if not template_id_str.isdigit():
        await callback.answer("ত্রুটি: অবৈধ টেমপ্লেট আইডি।", show_alert=True)
        return

    template = await get_template_by_id(int(template_id_str))
    if not template:
        await callback.answer("টেমপ্লেটটি পাওয়া যায়নি।", show_alert=True)
        return

    user = await upsert_user(callback.from_user.id)
    preferred_font = user.get("preferred_font")
    if preferred_font == "default" or not preferred_font:
        preferred_font = None

    await state.update_data(
        template_id=template["id"],
        file_id=template["file_id"],
        name=template["name"],
        variant="white_header",
        font_key=preferred_font,
        banner_id=None,
        is_clean=False,
    )
    await state.set_state(MemeFlowSG.waiting_for_text)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ বাতিল / Cancel", callback_data="cb_cancel")]]
    )

    prompt_text = (
        f"✍️ <b>'{template['name']}'</b> টেমপ্লেটের জন্য আপনার মিমের টেক্সট লিখুন:\n\n"
        f"💡 <i>টিপস:</i>\n"
        f"• আপনি সাধারণ বাংলা বা ইংরেজি টেক্সট লিখতে পারেন।\n"
        f"• ক্লাসিক ওভারলে মিমের জন্য উপরে ও নিচের লাইন ভাগ করতে <code>|</code> চিহ্ন দিন।\n"
        f"  (যেমন: <i>উপরে যা থাকবে | নিচে যা থাকবে</i>)"
    )

    await callback.message.answer(prompt_text, reply_markup=cancel_kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "cb_cancel")
async def handle_cancel_flow(callback: types.CallbackQuery, state: FSMContext):
    """Cancel current FSM state and return to idle."""
    await state.clear()
    await callback.answer("বাতিল করা হয়েছে।")
    await callback.message.edit_text("❌ মিম তৈরি বাতিল করা হয়েছে। নতুন মিম তৈরি করতে /start চাপুন।")

# ------------------------------------------------------------------------------
# Text Input & Initial Rendering (Bannerless by Default)
# ------------------------------------------------------------------------------

@router.message(MemeFlowSG.waiting_for_text, F.text)
async def handle_meme_text_input(message: types.Message, state: FSMContext):
    """Receive caption, generate meme in-memory, and dispatch with action controls."""
    data = await state.get_data()
    file_id = data.get("file_id")
    template_id = data.get("template_id")
    variant = data.get("variant", "white_header")
    font_key = data.get("font_key")

    text = message.text.strip()
    if not text:
        await message.answer("অনুগ্রহ করে কিছু টেক্সট লিখুন:")
        return

    # Security: Limit maximum caption length to prevent CPU denial of service
    MAX_CAPTION_LEN = 300
    if len(text) > MAX_CAPTION_LEN:
        await message.answer(
            f"❌ <b>ক্যাপশনটি অতিরিক্ত দীর্ঘ!</b>\nসর্বোচ্চ {MAX_CAPTION_LEN} অক্ষরের মধ্যে লিখুন (আপনার টেক্সট: {len(text)} অক্ষর)।",
            parse_mode="HTML",
        )
        return

    status_msg = await message.answer("⏳ <b>মিম রেন্ডার করা হচ্ছে...</b>", parse_mode="HTML")

    # 1. Download base template into memory
    template_bytes = await _fetch_telegram_file_bytes(message.bot, file_id)
    if not template_bytes:
        await status_msg.edit_text("❌ টেমপ্লেট ডাউনলোড করতে সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।")
        return

    # 2. Resolve typography & script
    script = font_manager.detect_script(text)
    font_path = font_manager.get_font_path(font_key, script=script)

    # 3. Check user custom watermark
    user = await get_user(message.from_user.id)
    watermark_bytes = None
    watermark_pos = "bottom_right"

    if user and user.get("watermark_enabled") and user.get("watermark_file_id"):
        watermark_pos = user.get("watermark_position", "bottom_right")
        watermark_bytes = await _fetch_telegram_file_bytes(message.bot, user["watermark_file_id"])

    # 4. Strict Opt-In Banner Check: Default is None
    banner_bytes = None

    # 5. Render meme in worker thread
    try:
        rendered_buffer = await render_meme(
            template_bytes=template_bytes,
            text=text,
            font_path=font_path,
            variant=variant,
            is_clean=False,
            watermark_bytes=watermark_bytes,
            watermark_pos=watermark_pos,
            banner_bytes=banner_bytes,
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ মিম তৈরি করতে ত্রুটি হয়েছে: {e}")
        return

    # 6. Increment popularity counter
    if template_id:
        await increment_template_usage(template_id)

    # 7. Store latest generation metadata in FSM state for interactive actions
    await state.update_data(
        last_text=text,
        last_font_key=font_key,
        last_variant=variant,
        last_banner_id=None,
    )

    photo_file = BufferedInputFile(rendered_buffer.getvalue(), filename="meme.jpg")
    caption = f"🎭 <b>{data.get('name', 'Meme')}</b>\n✨ <i>KBKH Meme Bot দ্বারা তৈরি</i>"

    await message.answer_photo(
        photo=photo_file,
        caption=caption,
        reply_markup=get_meme_actions_keyboard(has_banner=False),
        parse_mode="HTML",
    )
    await status_msg.delete()

# ------------------------------------------------------------------------------
# Post-Generation Interactive Callbacks
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "cb_clean")
async def handle_download_clean(callback: types.CallbackQuery, state: FSMContext):
    """Deliver an unbranded clean version without KBKH Group logo."""
    data = await state.get_data()
    file_id = data.get("file_id")
    text = data.get("last_text")

    if not file_id or not text:
        await callback.answer("সেশন পাওয়া যায়নি। নতুন করে মিম তৈরি করুন।", show_alert=True)
        return

    await callback.answer("📥 ক্লিন সংস্করণ প্রস্তুত হচ্ছে...")

    template_bytes = await _fetch_telegram_file_bytes(callback.message.bot, file_id)
    if not template_bytes:
        await callback.answer("টেমপ্লেট লোড করতে ব্যর্থ।", show_alert=True)
        return

    font_path = font_manager.get_font_path(data.get("last_font_key"), script=font_manager.detect_script(text))

    user = await get_user(callback.from_user.id)
    watermark_bytes = None
    watermark_pos = "bottom_right"
    if user and user.get("watermark_enabled") and user.get("watermark_file_id"):
        watermark_pos = user.get("watermark_position", "bottom_right")
        watermark_bytes = await _fetch_telegram_file_bytes(callback.message.bot, user["watermark_file_id"])

    rendered_buf = await render_meme(
        template_bytes=template_bytes,
        text=text,
        font_path=font_path,
        variant=data.get("last_variant", "white_header"),
        is_clean=True,  # Bypass logo placement
        watermark_bytes=watermark_bytes,
        watermark_pos=watermark_pos,
        banner_bytes=None,
    )

    photo_file = BufferedInputFile(rendered_buf.getvalue(), filename="clean_meme.jpg")
    await callback.message.reply_photo(
        photo=photo_file,
        caption="✨ <b>ক্লিন আনব্র্যান্ডেড সংস্করণ (লোগো ছাড়া)</b>",
        parse_mode="HTML",
    )

@router.callback_query(F.data == "cb_style_menu")
async def handle_style_menu(callback: types.CallbackQuery):
    """Present style selection menu."""
    style_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚪ হোয়াইট হেডার (Variant A)", callback_data="cb_set_style:white_header"),
            ],
            [
                InlineKeyboardButton(text="⚫ ডার্ক হেডার (Variant B)", callback_data="cb_set_style:dark_header"),
            ],
            [
                InlineKeyboardButton(text="🔲 ক্লাসিক ওভারলে (Variant C)", callback_data="cb_set_style:classic_overlay"),
            ],
            [
                InlineKeyboardButton(text="🔙 ফিরে যান", callback_data="cb_back_meme"),
            ],
        ]
    )
    await callback.message.edit_caption(
        caption="🎨 <b>পছন্দের স্টাইল নির্বাচন করুন:</b>",
        reply_markup=style_kb,
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_style:"))
async def handle_set_style(callback: types.CallbackQuery, state: FSMContext):
    """Switch meme visual variant and re-render in place."""
    new_style = callback.data.split(":", 1)[1]
    data = await state.get_data()
    file_id = data.get("file_id")
    text = data.get("last_text")

    if not file_id or not text:
        await callback.answer("সেশন মেয়াদোত্তীর্ণ।", show_alert=True)
        return

    await callback.answer("🔄 স্টাইল পরিবর্তন হচ্ছে...")
    await state.update_data(last_variant=new_style)

    template_bytes = await _fetch_telegram_file_bytes(callback.message.bot, file_id)
    font_path = font_manager.get_font_path(data.get("last_font_key"), script=font_manager.detect_script(text))

    user = await get_user(callback.from_user.id)
    watermark_bytes = None
    watermark_pos = "bottom_right"
    if user and user.get("watermark_enabled") and user.get("watermark_file_id"):
        watermark_pos = user.get("watermark_position", "bottom_right")
        watermark_bytes = await _fetch_telegram_file_bytes(callback.message.bot, user["watermark_file_id"])

    banner_bytes = None
    if data.get("last_banner_id"):
        banner = await get_banner_by_id(data["last_banner_id"])
        if banner:
            banner_bytes = await _fetch_telegram_file_bytes(callback.message.bot, banner["file_id"])

    rendered_buf = await render_meme(
        template_bytes=template_bytes,
        text=text,
        font_path=font_path,
        variant=new_style,
        is_clean=False,
        watermark_bytes=watermark_bytes,
        watermark_pos=watermark_pos,
        banner_bytes=banner_bytes,
    )

    photo_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme.jpg")
    await callback.message.edit_media(
        media=InputMediaPhoto(media=photo_file, caption="✨ স্টাইল সফলভাবে পরিবর্তিত হয়েছে!", parse_mode="HTML"),
        reply_markup=get_meme_actions_keyboard(has_banner=bool(banner_bytes)),
    )

@router.callback_query(F.data == "cb_font_menu")
async def handle_font_menu(callback: types.CallbackQuery):
    """Present font switching keyboard."""
    font_list = font_manager.get_font_list()
    keyboard_buttons = []
    row = []

    for key, display_name in font_list:
        row.append(InlineKeyboardButton(text=display_name, callback_data=f"cb_set_font:{key}"))
        if len(row) == 2:
            keyboard_buttons.append(row)
            row = []
    if row:
        keyboard_buttons.append(row)

    keyboard_buttons.append([InlineKeyboardButton(text="🔙 ফিরে যান", callback_data="cb_back_meme")])

    await callback.message.edit_caption(
        caption="🔤 <b>পছন্দের ফন্টটি নির্বাচন করুন:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_set_font:"))
async def handle_set_font(callback: types.CallbackQuery, state: FSMContext):
    """Apply newly selected font and re-render."""
    font_key = callback.data.split(":", 1)[1]
    data = await state.get_data()
    file_id = data.get("file_id")
    text = data.get("last_text")

    if not file_id or not text:
        await callback.answer("সেশন মেয়াদোত্তীর্ণ।", show_alert=True)
        return

    await callback.answer("🔤 ফন্ট পরিবর্তন হচ্ছে...")
    await state.update_data(last_font_key=font_key)

    template_bytes = await _fetch_telegram_file_bytes(callback.message.bot, file_id)
    font_path = font_manager.get_font_path(font_key, script=font_manager.detect_script(text))

    user = await get_user(callback.from_user.id)
    watermark_bytes = None
    watermark_pos = "bottom_right"
    if user and user.get("watermark_enabled") and user.get("watermark_file_id"):
        watermark_pos = user.get("watermark_position", "bottom_right")
        watermark_bytes = await _fetch_telegram_file_bytes(callback.message.bot, user["watermark_file_id"])

    banner_bytes = None
    if data.get("last_banner_id"):
        banner = await get_banner_by_id(data["last_banner_id"])
        if banner:
            banner_bytes = await _fetch_telegram_file_bytes(callback.message.bot, banner["file_id"])

    rendered_buf = await render_meme(
        template_bytes=template_bytes,
        text=text,
        font_path=font_path,
        variant=data.get("last_variant", "white_header"),
        is_clean=False,
        watermark_bytes=watermark_bytes,
        watermark_pos=watermark_pos,
        banner_bytes=banner_bytes,
    )

    photo_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme.jpg")
    await callback.message.edit_media(
        media=InputMediaPhoto(media=photo_file, caption="✨ ফন্ট সফলভাবে পরিবর্তিত হয়েছে!", parse_mode="HTML"),
        reply_markup=get_meme_actions_keyboard(has_banner=bool(banner_bytes)),
    )

@router.callback_query(F.data == "cb_banner_menu")
async def handle_banner_menu(callback: types.CallbackQuery):
    """Present opt-in banner choices from the database."""
    banners = await get_all_banners()
    if not banners:
        await callback.answer("⚠️ বর্তমানে কোনো প্রমোশনাল ব্যানার যুক্ত করা নেই।", show_alert=True)
        return

    keyboard_buttons = []
    for b in banners:
        keyboard_buttons.append([
            InlineKeyboardButton(text=f"📢 {b['name']}", callback_data=f"cb_apply_banner:{b['id']}")
        ])
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 ফিরে যান", callback_data="cb_back_meme")])

    await callback.message.edit_caption(
        caption="📢 <b>নিচের তালিকা থেকে প্রমোশনাল ব্যানার নির্বাচন করুন:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cb_apply_banner:"))
async def handle_apply_banner(callback: types.CallbackQuery, state: FSMContext):
    """Attach selected promotional banner to bottom canvas."""
    banner_id_str = callback.data.split(":", 1)[1]
    banner = await get_banner_by_id(int(banner_id_str))
    if not banner:
        await callback.answer("ব্যানারটি পাওয়া যায়নি।", show_alert=True)
        return

    data = await state.get_data()
    file_id = data.get("file_id")
    text = data.get("last_text")

    if not file_id or not text:
        await callback.answer("সেশন মেয়াদোত্তীর্ণ।", show_alert=True)
        return

    await callback.answer("➕ ব্যানার যুক্ত করা হচ্ছে...")
    await state.update_data(last_banner_id=banner["id"])

    template_bytes = await _fetch_telegram_file_bytes(callback.message.bot, file_id)
    banner_bytes = await _fetch_telegram_file_bytes(callback.message.bot, banner["file_id"])
    font_path = font_manager.get_font_path(data.get("last_font_key"), script=font_manager.detect_script(text))

    user = await get_user(callback.from_user.id)
    watermark_bytes = None
    watermark_pos = "bottom_right"
    if user and user.get("watermark_enabled") and user.get("watermark_file_id"):
        watermark_pos = user.get("watermark_position", "bottom_right")
        watermark_bytes = await _fetch_telegram_file_bytes(callback.message.bot, user["watermark_file_id"])

    rendered_buf = await render_meme(
        template_bytes=template_bytes,
        text=text,
        font_path=font_path,
        variant=data.get("last_variant", "white_header"),
        is_clean=False,
        watermark_bytes=watermark_bytes,
        watermark_pos=watermark_pos,
        banner_bytes=banner_bytes,
    )

    photo_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_banner.jpg")
    await callback.message.edit_media(
        media=InputMediaPhoto(media=photo_file, caption="✅ প্রমোশনাল ব্যানার যুক্ত করা হয়েছে!", parse_mode="HTML"),
        reply_markup=get_meme_actions_keyboard(has_banner=True),
    )

@router.callback_query(F.data == "cb_remove_banner")
async def handle_remove_banner(callback: types.CallbackQuery, state: FSMContext):
    """Remove attached banner and restore clean bannerless meme."""
    data = await state.get_data()
    file_id = data.get("file_id")
    text = data.get("last_text")

    if not file_id or not text:
        await callback.answer("সেশন মেয়াদোত্তীর্ণ।", show_alert=True)
        return

    await callback.answer("✖️ ব্যানার সরানো হচ্ছে...")
    await state.update_data(last_banner_id=None)

    template_bytes = await _fetch_telegram_file_bytes(callback.message.bot, file_id)
    font_path = font_manager.get_font_path(data.get("last_font_key"), script=font_manager.detect_script(text))

    user = await get_user(callback.from_user.id)
    watermark_bytes = None
    watermark_pos = "bottom_right"
    if user and user.get("watermark_enabled") and user.get("watermark_file_id"):
        watermark_pos = user.get("watermark_position", "bottom_right")
        watermark_bytes = await _fetch_telegram_file_bytes(callback.message.bot, user["watermark_file_id"])

    rendered_buf = await render_meme(
        template_bytes=template_bytes,
        text=text,
        font_path=font_path,
        variant=data.get("last_variant", "white_header"),
        is_clean=False,
        watermark_bytes=watermark_bytes,
        watermark_pos=watermark_pos,
        banner_bytes=None,  # Reset banner
    )

    photo_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme.jpg")
    await callback.message.edit_media(
        media=InputMediaPhoto(media=photo_file, caption="✨ ব্যানার সরানো হয়েছে!", parse_mode="HTML"),
        reply_markup=get_meme_actions_keyboard(has_banner=False),
    )

@router.callback_query(F.data == "cb_back_meme")
async def handle_back_meme(callback: types.CallbackQuery, state: FSMContext):
    """Return to default meme action keyboard."""
    data = await state.get_data()
    has_banner = bool(data.get("last_banner_id"))
    await callback.message.edit_caption(
        caption="🎭 <b>মিম অ্যাকশন মেনু:</b>",
        reply_markup=get_meme_actions_keyboard(has_banner=has_banner),
        parse_mode="HTML",
    )
    await callback.answer()
