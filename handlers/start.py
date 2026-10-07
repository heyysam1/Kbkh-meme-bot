from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.queries import upsert_user

router = Router(name="start_router")

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the primary navigation inline keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 মিম খুঁজুন / Search", callback_data="menu_search"),
                InlineKeyboardButton(text="📁 টেমপ্লেট / Templates", callback_data="menu_browse"),
            ],
            [
                InlineKeyboardButton(text="⚙️ সেটিংস / Settings", callback_data="menu_settings"),
                InlineKeyboardButton(text="ℹ️ সাহায্য / Help", callback_data="menu_help"),
            ],
        ]
    )

@router.message(CommandStart())
async def handle_start(message: types.Message):
    """Handle /start command, register user profile, and present welcome interface."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    await upsert_user(user_id)

    welcome_text = (
        f"👋 <b>স্বাগতম, {message.from_user.full_name}!</b>\n\n"
        f"🤖 <b>KBKH Telegram Meme Bot</b>-এ আপনাকে স্বাগতম।\n"
        f"এখানে আপনি সহজে ট্রেন্ডিং মিম টেমপ্লেট খুঁজে পাবেন এবং মুহূর্তেই প্রফেশনাল মিম তৈরি করতে পারবেন।\n\n"
        f"✨ <b>মূল সুবিধাসমূহ:</b>\n"
        f"• 🎨 <b>৩টি আধুনিক স্টাইল:</b> হোয়াইট হেডার, ডার্ক হেডার এবং ক্লাসিক ইমপ্যাক্ট মিম।\n"
        f"• 🔤 <b>১৭টি ফন্ট:</b> সেরা বাংলা ও ক্লাসিক ইংরেজি মিম টাইপোগ্রাফি।\n"
        f"• 🔍 <b>স্মার্ট সার্চ:</b> যেকোনো মিম সহজে খুঁজে বের করার ব্যবস্থা।\n"
        f"• 📥 <b>র' টেমপ্লেট:</b> ব্ল্যাঙ্ক টেমপ্লেট সরাসরি ফটো ও ডকুমেন্ট আকারে ডাউনলোড।\n"
        f"• 🏷️ <b>কাস্টম ওয়াটারমার্ক:</b> আপনার নিজস্ব লোগো/ওয়াটারমার্ক যুক্ত করার সুবিধা।\n"
        f"• 🛡️ <b>স্মার্ট ব্র্যান্ডিং:</b> অটো-কনট্রাস্ট সহ KBKH Group লোগো (অথবা আনব্র্যান্ডেড ক্লিন এক্সপোর্ট)।\n\n"
        f"👇 নিচে থেকে একটি অপশন নির্বাচন করুন:"
    )

    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

@router.message(Command("help"))
async def handle_help(message: types.Message):
    """Handle /help command with usage guidelines."""
    help_text = (
        "📖 <b>বট ব্যবহারের নির্দেশিকা:</b>\n\n"
        "• <b>/start</b> - মূল মেনু ও নেভিগেশন\n"
        "• <b>/template</b> - সমস্ত টেমপ্লেটের ক্যাটালগ ব্রাউজ করুন\n"
        "• <b>/search &lt;মিমের নাম&gt;</b> - লোকাল ও ট্রেন্ডিং মিম সার্চ করুন\n"
        "• <b>/random</b> - র্যান্ডম মিম টেমপ্লেট পান\n"
        "• <b>/settings</b> - নিজস্ব ওয়াটারমার্ক ও প্রিয় ফন্ট নির্বাচন করুন\n"
        "• <b>/help</b> - এই সাহায্য বার্তাটি প্রদর্শন করুন\n\n"
        "💡 <b>মিম তৈরির টিপস:</b>\n"
        "- ক্লাসিক মিমের ক্ষেত্রে উপরে ও নিচের টেক্সট আলাদা করতে <code>|</code> চিহ্ন ব্যবহার করুন।\n"
        "  (যেমন: <i>উপরে টেক্সট | নিচে টেক্সট</i>)\n"
        "- মিম তৈরি হওয়ার পর [🔤 Change Font] বাটনে ক্লিক করে সাথে সাথে ফন্ট পরিবর্তন করতে পারেন।"
    )
    await message.answer(help_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

@router.callback_query(lambda c: c.data == "menu_help")
async def cb_help(callback: types.CallbackQuery):
    """Callback for in-menu help navigation."""
    await callback.answer()
    await handle_help(callback.message)
