import io
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
from handlers.meme_flow import MemeFlowSG
from services.search_engine import (
    hybrid_search_templates,
    get_external_template,
    fetch_external_image_bytes,
)

router = Router(name="search_flow_router")

def build_template_choice_card(template: dict) -> InlineKeyboardMarkup:
    """Construct two-way action card for a local template (Create Meme vs Download Raw)."""
    t_id = template["id"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎨 মিম তৈরি করুন / Create", callback_data=f"btn_create:{t_id}"),
                InlineKeyboardButton(text="📥 টেমপ্লেট ডাউনলোড / Raw", callback_data=f"btn_raw:{t_id}"),
            ]
        ]
    )

def build_external_choice_card(ext_id: str) -> InlineKeyboardMarkup:
    """Construct action card for an external template (Use Template vs Download)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎨 মিম তৈরি করুন / Use Template", callback_data=f"btn_ext_use:{ext_id}"),
                InlineKeyboardButton(text="📥 ডাউনলোড / Download", callback_data=f"btn_ext_dl:{ext_id}"),
            ]
        ]
    )

@router.callback_query(F.data == "menu_search")
async def cb_menu_search(callback: types.CallbackQuery):
    """Prompt user to type their search query."""
    await callback.answer()
    await callback.message.answer(
        "🔍 <b>মিম সার্চ:</b>\n"
        "আপনি যে মিম টেমপ্লেটটি খুঁজছেন তার নাম বা কি-ওয়ার্ড লিখে মেসেজ পাঠান।\n\n"
        "<i>উদাহরণ:</i> <code>tony stark</code>, <code>drake</code>, <code>cat</code>, <code>doge</code>",
        parse_mode="HTML",
    )

@router.callback_query(F.data == "menu_browse")
@router.message(Command("template", "templates"))
async def handle_browse_templates(event: types.Message | types.CallbackQuery):
    """Display catalog of available/trending meme templates."""
    message = event if isinstance(event, types.Message) else event.message
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    templates = await get_all_templates(limit=8, offset=0)
    if not templates:
        await message.answer(
            "📂 <b>টেমপ্লেট ক্যাটালগ খালি!</b>\n\n"
            "অ্যাডমিন চ্যানেল থেকে নতুন টেমপ্লেট আপলোড করার সাথে সাথে এখানে পাওয়া যাবে।\n"
            "💡 এছাড়াও আপনি <code>/search &lt;name&gt;</code> দিয়ে সরাসরি যেকোনো পাবলিক মিম খুঁজতে পারেন!",
            parse_mode="HTML",
        )
        return

    await message.answer("📁 <b>জনপ্রিয় ও ট্রেন্ডিং মিম টেমপ্লেট তালিকা:</b>", parse_mode="HTML")

    for t in templates:
        trending_badge = " 🔥" if t.get("is_trending") else ""
        title = t.get("title") or t.get("name") or "Template"
        caption = f"🎭 <b>{title}</b>{trending_badge}\n🏷️ <i>ট্যাগ: {t.get('tags', '')}</i>"
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=t["file_id"],
                caption=caption,
                reply_markup=build_template_choice_card(t),
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(
                caption,
                reply_markup=build_template_choice_card(t),
                parse_mode="HTML",
            )

@router.message(Command("random"))
async def handle_random_template(message: types.Message):
    """Fetch and present a random meme template from the catalog."""
    template = await get_random_template()
    if template:
        title = template.get("title") or template.get("name") or "Random Template"
        caption = (
            f"🎲 <b>র্যান্ডম মিম টেমপ্লেট:</b>\n\n"
            f"🎭 <b>{title}</b>\n"
            f"🏷️ <i>ট্যাগ: {template.get('tags', '')}</i>"
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
    from services.search_engine import _fetch_imgflip_memes
    import random
    memes = await _fetch_imgflip_memes()
    if memes:
        m = random.choice(memes)
        ext_id = f"ext_if_{m['id']}"
        from services.search_engine import _EXTERNAL_REGISTRY
        _EXTERNAL_REGISTRY[ext_id] = {
            "id": ext_id,
            "title": m["name"],
            "name": m["name"],
            "url": m["url"],
            "is_external": True,
            "source": "Imgflip",
        }
        caption = (
            f"🎲 <b>র্যান্ডম পাবলিক মিম টেমপ্লেট:</b>\n\n"
            f"🎭 <b>{m['name']}</b>\n"
            f"🌐 <i>উৎস: Imgflip</i>"
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

    await message.answer("⚠️ বর্তমানে কোনো টেমপ্লেট পাওয়া যায়নি। কিছু সময় পর আবার চেষ্টা করুন।")

@router.message(Command("search"))
async def handle_search_command(message: types.Message):
    """Handle /search <keyword> command."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("অনুগ্রহ করে সার্চ কি-ওয়ার্ড লিখুন। যেমন: <code>/search drake</code>", parse_mode="HTML")
        return

    query = parts[1].strip()
    await process_search_query(message, query)

@router.message(F.text, ~F.text.startswith("/"))
async def handle_text_search_fallback(message: types.Message):
    """Catch general text queries outside active FSM states as search attempts."""
    query = message.text.strip()
    if len(query) < 2:
        return
    await process_search_query(message, query)

async def process_search_query(message: types.Message, query: str):
    """Run hybrid search engine (local + external fallback) and present results."""
    wait_msg = await message.answer(f"🔍 <b>'{query}'</b> এর জন্য টেমপ্লেট খোঁজা হচ্ছে...", parse_mode="HTML")
    search_data = await hybrid_search_templates(query, limit=4)
    await wait_msg.delete()

    local_results = search_data["local"]
    external_results = search_data["external"]

    if not local_results and not external_results:
        await message.answer(
            f"❌ <b>'{query}'</b> এর সাথে মিলে এমন কোনো টেমপ্লেট পাওয়া যায়নি।\n\n"
            f"💡 অন্য কোনো কি-ওয়ার্ড দিয়ে খুঁজুন অথবা /template ব্রাউজ করুন।",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"🎯 <b>'{query}'</b> এর জন্য সেরা ফলাফলসমূহ:\n"
        f"নিচে থেকে আপনার কাঙ্ক্ষিত মিমটি নির্বাচন করুন:",
        parse_mode="HTML",
    )

    # 1. Present local catalog matches
    for candidate in local_results:
        score_pct = int(candidate.get("search_score", 0.0) * 100)
        trending_badge = " 🔥" if candidate.get("is_trending") else ""
        title = candidate.get("title") or candidate.get("name") or "Template"
        caption = (
            f"🎭 <b>{title}</b>{trending_badge}\n"
            f"🎯 <i>ম্যাচ স্কোর: {score_pct}%</i>\n"
            f"🏷️ <i>ট্যাগ: {candidate.get('tags', '')}</i>"
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
            f"🌐 <b>{title}</b>\n"
            f"📦 <i>উৎস: {source} (পাবলিক টেমপ্লেট)</i>\n"
            f"💡 <i>ক্লিক করে মুহূর্তেই মিম তৈরি বা ডাউনলোড করুন</i>"
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
        await callback.answer("টেমপ্লেটের তথ্য পাওয়া যায়নি। অনুগ্রহ করে আবার সার্চ করুন।", show_alert=True)
        return

    await callback.answer("⏳ টেমপ্লেট প্রস্তুত করা হচ্ছে...")
    status_msg = await callback.message.answer("⏳ <b>অনলাইন টেমপ্লেট ডাউনলোড ও প্রসেস করা হচ্ছে...</b>", parse_mode="HTML")

    # Fetch image bytes in memory
    img_bytes = await fetch_external_image_bytes(ext_item["url"])
    if not img_bytes:
        await status_msg.edit_text("❌ টেমপ্লেটটি ডাউনলোড করা সম্ভব হয়নি। অনুগ্রহ করে অন্যটি নির্বাচন করুন।")
        return

    # Upload to Telegram to obtain canonical file_id
    title = ext_item.get("title") or ext_item.get("name") or "External Template"
    input_file = BufferedInputFile(img_bytes, filename="template.jpg")
    sent = await callback.message.answer_photo(
        photo=input_file,
        caption=f"✅ <b>{title}</b> প্রস্তুত করা হয়েছে!",
        parse_mode="HTML",
    )
    file_id = sent.photo[-1].file_id
    file_unique_id = sent.photo[-1].file_unique_id
    await status_msg.delete()

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
    )

    # Initialize FSM State
    await upsert_user(callback.from_user.id)
    await state.update_data(
        template_id=template_id,
        file_id=file_id,
        media_type="photo",
        template_name=title,
        font_key="Impact.ttf",
        variant="white_header",
        watermark_pos="bottom_right",
        has_banner=False,
    )
    await state.set_state(MemeFlowSG.waiting_for_text)

    await callback.message.answer(
        f"✍️ <b>'{title}'</b> নির্বাচিত হয়েছে!\n\n"
        f"এখন আপনার মিমের জন্য <b>টেক্সট / ক্যাপশন</b> লিখে পাঠান:\n\n"
        f"💡 <i>টিপ: ক্লাসিক স্টাইলের ক্ষেত্রে উপরে ও নিচের টেক্সট আলাদা করতে <code>|</code> চিহ্ন ব্যবহার করুন।</i>\n"
        f"<i>(যেমন: <code>উপরে লেখা | নিচে লেখা</code>)</i>",
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("btn_ext_dl:"))
async def handle_external_download_template(callback: types.CallbackQuery):
    """Directly download external template bytes and deliver to user."""
    ext_id = callback.data.split(":", 1)[1]
    ext_item = get_external_template(ext_id)
    if not ext_item:
        await callback.answer("টেমপ্লেটের তথ্য পাওয়া যায়নি।", show_alert=True)
        return

    await callback.answer("📥 ডাউনলোড হচ্ছে...")
    img_bytes = await fetch_external_image_bytes(ext_item["url"])
    if not img_bytes:
        await callback.message.answer("❌ টেমপ্লেটটি ডাউনলোড করা সম্ভব হয়নি।")
        return

    title = ext_item.get("title") or ext_item.get("name") or "Template"
    file_photo = BufferedInputFile(img_bytes, filename=f"{title}.jpg")
    file_doc = BufferedInputFile(img_bytes, filename=f"{title}.jpg")

    await callback.message.answer_photo(
        photo=file_photo,
        caption=f"📷 <b>{title}</b> (Preview)",
        parse_mode="HTML",
    )
    await callback.message.answer_document(
        document=file_doc,
        caption=f"📁 <b>{title}</b> (Original Image)",
        parse_mode="HTML",
    )
