from typing import Optional
from aiogram import Router, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.queries import get_template_by_id, get_user_lang, upsert_user
from handlers.editor import EditorSG
from services.i18n import t

router = Router(name="start_router")

# log_template_use is added by a sibling agent; import defensively so the bot
# keeps working until it lands.
try:
    from database.queries import log_template_use
except ImportError:  # pragma: no cover
    log_template_use = None

def get_main_menu_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build the primary navigation inline keyboard with zero emojis."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("start.browse_catalog", lang), callback_data="tpl_grid:1"),
                InlineKeyboardButton(text=t("start.add_template", lang), callback_data="action_add_template"),
            ],
            [
                InlineKeyboardButton(text=t("start.search_memes", lang), callback_data="menu_search"),
                InlineKeyboardButton(text=t("start.settings", lang), callback_data="menu_settings"),
            ],
            [
                InlineKeyboardButton(text=t("start.help", lang), callback_data="menu_help"),
            ],
        ]
    )

@router.message(CommandStart(deep_link=True), StateFilter("*"), flags={"state": "*"})
async def handle_deep_link(message: types.Message, state: FSMContext, command: CommandObject):
    """Handle /start deep links (e.g. t.me/<bot>?start=tpl_123) by jumping into the editor.

    Registered above handle_start because plain CommandStart() also matches
    deep-link payloads; aiogram checks handlers in registration order.
    """
    lang = await get_user_lang(message.from_user.id)
    args = (command.args or "").strip()
    template_id_str = args[4:] if args.startswith("tpl_") else ""
    template = (
        await get_template_by_id(int(template_id_str))
        if template_id_str.isdigit()
        else None
    )
    if not template:
        if args:
            await message.answer(t("start.template_not_found", lang))
        await handle_start(message, state)
        return

    user = await upsert_user(message.from_user.id)
    preferred_font = user.get("preferred_font")
    if preferred_font == "default" or not preferred_font:
        preferred_font = None

    if log_template_use is not None:
        try:
            await log_template_use(message.from_user.id, template["id"])
        except Exception:
            pass

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
        inline_keyboard=[[InlineKeyboardButton(text=t("misc.cancel", lang), callback_data="edit:cancel")]]
    )

    title = template.get("title") or template.get("name") or "Template"
    await message.answer(
        t("start.editor_prompt", lang).format(title=title),
        reply_markup=cancel_kb,
    )

@router.message(CommandStart(), StateFilter("*"), flags={"state": "*"})
@router.message(Command("start"), StateFilter("*"), flags={"state": "*"})
async def handle_start(message: types.Message, state: FSMContext):
    """Handle /start command, clear any active FSM state, and present dashboard."""
    await state.clear()
    user_id = message.from_user.id
    await upsert_user(user_id)
    lang = await get_user_lang(user_id)

    welcome_text = t("start.welcome", lang).format(name=message.from_user.full_name)

    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard(lang), parse_mode="HTML")

@router.message(Command("help"), StateFilter("*"), flags={"state": "*"})
async def handle_help(message: types.Message, state: Optional[FSMContext] = None, user_id: Optional[int] = None):
    """Handle /help command across any state with zero-emoji usage guidelines."""
    if state:
        await state.clear()
    uid = user_id or (message.from_user.id if message.from_user else 0)
    lang = await get_user_lang(uid)
    await message.answer(t("start.help_text", lang), reply_markup=get_main_menu_keyboard(lang), parse_mode="HTML")

@router.message(Command("cancel"), StateFilter("*"), flags={"state": "*"})
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Universal cancellation command that cleanly terminates any active session."""
    await state.clear()
    lang = await get_user_lang(message.from_user.id)
    await message.answer(
        t("misc.cancelled_menu", lang),
        reply_markup=get_main_menu_keyboard(lang),
    )

@router.callback_query(lambda c: c.data == "menu_help")
async def cb_help(callback: types.CallbackQuery, state: Optional[FSMContext] = None):
    """Callback for in-menu help navigation."""
    await callback.answer()
    if state:
        await state.clear()
    await handle_help(callback.message, state, user_id=callback.from_user.id)
