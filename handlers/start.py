from typing import Optional
from aiogram import Router, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.queries import upsert_user

router = Router(name="start_router")

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the primary navigation inline keyboard with zero emojis."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[Browse Catalog]", callback_data="cb_grid_page:1"),
                InlineKeyboardButton(text="[+ Add Template]", callback_data="cb_add_template"),
            ],
            [
                InlineKeyboardButton(text="[Search Memes]", callback_data="menu_search"),
                InlineKeyboardButton(text="[Settings]", callback_data="menu_settings"),
            ],
            [
                InlineKeyboardButton(text="[Help / Guide]", callback_data="menu_help"),
            ],
        ]
    )

@router.message(CommandStart(), StateFilter("*"), flags={"state": "*"})
@router.message(Command("start"), StateFilter("*"), flags={"state": "*"})
async def handle_start(message: types.Message, state: FSMContext):
    """Handle /start command, clear any active FSM state, and present dashboard."""
    await state.clear()
    user_id = message.from_user.id
    await upsert_user(user_id)

    welcome_text = (
        f"<b>[KBKH MEME ENGINE - DASHBOARD]</b>\n\n"
        f"Welcome, {message.from_user.full_name}!\n\n"
        f"Production-grade meme creation, template repository, and real-time canvas engine.\n\n"
        f"<b>Core Capabilities:</b>\n"
        f"• Layout Variants: Overlay, Top Banner, Bottom Banner, Breaking News Ticker\n"
        f"• Typography Engine: High-contrast stroke, Raqm Bengali complex script, 10 curated fonts\n"
        f"• Canvas FX: Deepfry, Grayscale, Color Inversion\n"
        f"• Watermark Control: Dynamic diagonal scaling (0.5x - 2.0x), opacity, 5 anchor positions\n"
        f"• Lossless Export: High-res photo or uncompressed Telegram document\n"
        f"• User Ingestion: Direct template submission with multi-channel support\n\n"
        f"Select an option below to proceed:"
    )

    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

@router.message(Command("help"), StateFilter("*"), flags={"state": "*"})
async def handle_help(message: types.Message, state: Optional[FSMContext] = None):
    """Handle /help command across any state with zero-emoji usage guidelines."""
    if state:
        await state.clear()
    help_text = (
        "<b>[USAGE GUIDE & SHORTCUTS]</b>\n\n"
        "<b>Navigation Commands:</b>\n"
        "• /start - Return to main dashboard\n"
        "• /template - Open multi-column catalog grid\n"
        "• /random - Fetch a random meme template\n"
        "• /add_template - Upload a new meme template to catalog\n"
        "• /search &lt;query&gt; - Search templates with hybrid fallback\n"
        "• /settings - Customize watermark and font preferences\n"
        "• /cancel - Abort active editing or upload session\n"
        "• /admin - Administrative management panel (Admins only)\n\n"
        "<b>Editing Tips:</b>\n"
        "• For 2-line classic overlay, split text with '|' (e.g., Top Text | Bottom Text)\n"
        "• Live editor lets you toggle layout, color, stroke, casing, filters, and watermark in real time\n"
        "• Export directly as Photo or uncompressed Document"
    )
    await message.answer(help_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

@router.message(Command("cancel"), StateFilter("*"), flags={"state": "*"})
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Universal cancellation command that cleanly terminates any active session."""
    await state.clear()
    await message.answer(
        "[Action cancelled. Returned to main menu.]",
        reply_markup=get_main_menu_keyboard(),
    )

@router.callback_query(lambda c: c.data == "menu_help")
async def cb_help(callback: types.CallbackQuery, state: Optional[FSMContext] = None):
    """Callback for in-menu help navigation."""
    await callback.answer()
    if state:
        await state.clear()
    await handle_help(callback.message, state)
