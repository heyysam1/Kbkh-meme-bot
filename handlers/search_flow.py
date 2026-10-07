from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.queries import get_all_templates, get_trending_templates
from services.search_engine import search_templates

router = Router(name="search_flow_router")

def build_template_choice_card(template: dict) -> InlineKeyboardMarkup:
    """Construct two-way action card for a template (Create Meme vs Download Raw)."""
    t_id = template["id"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎨 মিম তৈরি করুন / Create", callback_data=f"btn_create:{t_id}"),
                InlineKeyboardButton(text="📥 টেমপ্লেট ডাউনলোড / Raw", callback_data=f"btn_raw:{t_id}"),
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
        "<i>উদাহরণ:</i> <code>tony stark</code>, <code>drake</code>, <code>cat</code>",
        parse_mode="HTML",
    )

@router.callback_query(F.data == "menu_browse")
@router.message(Command("templates"))
async def handle_browse_templates(event: types.Message | types.CallbackQuery):
    """Display catalog of available/trending meme templates."""
    message = event if isinstance(event, types.Message) else event.message
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    templates = await get_all_templates(limit=8, offset=0)
    if not templates:
        await message.answer(
            "📂 <b>টেমপ্লেট ক্যাটালগ খালি!</b>\n\n"
            "অ্যাডমিন চ্যানেল থেকে নতুন টেমপ্লেট আপলোড করার সাথে সাথে এখানে পাওয়া যাবে।",
            parse_mode="HTML",
        )
        return

    await message.answer("📁 <b>জনপ্রিয় ও ট্রেন্ডিং মিম টেমপ্লেট তালিকা:</b>", parse_mode="HTML")

    for t in templates:
        trending_badge = " 🔥" if t.get("is_trending") else ""
        caption = f"🎭 <b>{t['name']}</b>{trending_badge}\n🏷️ <i>ট্যাগ: {t.get('tags', '')}</i>"
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=t["file_id"],
                caption=caption,
                reply_markup=build_template_choice_card(t),
                parse_mode="HTML",
            )
        except Exception:
            # Fallback if photo preview fails
            await message.answer(
                caption,
                reply_markup=build_template_choice_card(t),
                parse_mode="HTML",
            )

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
    # Ignore single character accidental inputs
    if len(query) < 2:
        return
    await process_search_query(message, query)

async def process_search_query(message: types.Message, query: str):
    """Run search engine, resolve aliases, and present top multi-choice options."""
    wait_msg = await message.answer(f"🔍 <b>'{query}'</b> এর জন্য টেমপ্লেট খোঁজা হচ্ছে...", parse_mode="HTML")
    results = await search_templates(query, limit=4)
    await wait_msg.delete()

    if not results:
        await message.answer(
            f"❌ <b>'{query}'</b> এর সাথে মিলে এমন কোনো টেমপ্লেট পাওয়া যায়নি।\n\n"
            f"💡 অন্য কোনো কি-ওয়ার্ড দিয়ে খুঁজুন অথবা /templates ব্রাউজ করুন।",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"🎯 <b>'{query}'</b> এর জন্য সেরা ফলাফলসমূহ:\n"
        f"নিচে থেকে আপনার কাঙ্ক্ষিত মিমটি নির্বাচন করুন:",
        parse_mode="HTML",
    )

    for candidate in results:
        score_pct = int(candidate.get("search_score", 0.0) * 100)
        trending_badge = " 🔥" if candidate.get("is_trending") else ""
        caption = (
            f"🎭 <b>{candidate['name']}</b>{trending_badge}\n"
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
