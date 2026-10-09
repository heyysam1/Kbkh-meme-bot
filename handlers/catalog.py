import math
from typing import Optional
from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from database.queries import (
    get_templates_paginated,
    get_templates_count,
    get_template_by_id,
    delete_template,
    get_random_template,
    get_user_lang,
    is_favorite,
    add_favorite,
    remove_favorite,
    get_favorites,
)
from services.i18n import t

router = Router(name="catalog_router")

# get_recent_templates / log_template_use are added by a sibling agent;
# import defensively so the bot keeps working until they land.
try:
    from database.queries import get_recent_templates
except ImportError:  # pragma: no cover
    get_recent_templates = None

PAGE_SIZE = 6

def build_catalog_grid_keyboard(templates: list, page: int, total_pages: int, lang: str = "bn") -> InlineKeyboardMarkup:
    """Build multi-column grid keyboard (2 items per row) with pagination controls."""
    builder = InlineKeyboardBuilder()

    # 1. Add template buttons (2 per row)
    for tpl in templates:
        title = tpl.get("title") or tpl.get("name") or "Template"
        display_label = title[:16] + ".." if len(title) > 18 else title
        builder.button(text=display_label, callback_data=f"view_tpl:{tpl['id']}:{page}")

    builder.adjust(2)

    # 2. Row 1: Pagination controls
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text=t("catalog.prev", lang), callback_data=f"tpl_grid:{page - 1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text=t("catalog.prev_disabled", lang), callback_data="noop"))

    nav_buttons.append(InlineKeyboardButton(text=t("catalog.page", lang).format(page=page, total=total_pages), callback_data="noop"))

    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text=t("catalog.next", lang), callback_data=f"tpl_grid:{page + 1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text=t("catalog.next_disabled", lang), callback_data="noop"))

    builder.row(*nav_buttons)

    # 3. Row 2: Secondary actions
    builder.row(
        InlineKeyboardButton(text=t("start.add_template", lang), callback_data="action_add_template"),
        InlineKeyboardButton(text=t("catalog.search", lang), callback_data="menu_search"),
        InlineKeyboardButton(text=t("catalog.recent", lang), callback_data="recent_grid"),
        InlineKeyboardButton(text=t("misc.close", lang), callback_data="close_catalog"),
    )

    return builder.as_markup()

def build_template_detail_keyboard(template_id: int, page: int, is_admin_user: bool = False, is_fav: bool = False, lang: str = "bn") -> InlineKeyboardMarkup:
    """Action buttons for template detail view."""
    buttons = [
        [
            InlineKeyboardButton(text=t("misc.create_meme", lang), callback_data=f"btn_create:{template_id}"),
            InlineKeyboardButton(text=t("catalog.download_raw", lang), callback_data=f"btn_raw:{template_id}"),
        ],
        [
            InlineKeyboardButton(
                text=t("catalog.remove_fav", lang) if is_fav else t("catalog.add_fav", lang),
                callback_data=f"fav_toggle:{template_id}:{page}",
            ),
        ],
        [
            InlineKeyboardButton(text=t("catalog.wm_settings", lang), callback_data="menu_settings"),
            InlineKeyboardButton(text=t("catalog.back_grid", lang), callback_data=f"tpl_grid:{page}"),
        ],
    ]

    if is_admin_user:
        buttons.append([InlineKeyboardButton(text=t("catalog.delete_tpl", lang), callback_data=f"admin_del_tpl:{template_id}:{page}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_random_keyboard(template_id: int, is_admin_user: bool = False, lang: str = "bn") -> InlineKeyboardMarkup:
    """Action buttons for a delivered random template, including a re-roll button."""
    buttons = [
        [
            InlineKeyboardButton(text=t("misc.create_meme", lang), callback_data=f"btn_create:{template_id}"),
            InlineKeyboardButton(text=t("catalog.download_raw", lang), callback_data=f"btn_raw:{template_id}"),
        ],
        [
            InlineKeyboardButton(text=t("catalog.another_random", lang), callback_data="btn_random_again"),
        ],
    ]

    if is_admin_user:
        buttons.append([InlineKeyboardButton(text=t("catalog.delete_tpl", lang), callback_data=f"admin_del_tpl:{template_id}:1")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(F.data == "close_catalog")
async def handle_close_catalog(callback: types.CallbackQuery):
    """Delete catalog message."""
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "noop")
async def handle_noop(callback: types.CallbackQuery):
    """No-operation button handler."""
    await callback.answer()

@router.message(Command("template", "templates"), StateFilter("*"), flags={"state": "*"})
@router.callback_query(F.data == "menu_browse")
@router.callback_query(F.data.startswith("tpl_grid:"))
async def handle_catalog_grid(event: types.Message | types.CallbackQuery, state: Optional[FSMContext] = None):
    """Render multi-column catalog grid and clear any active state if triggered via command."""
    if state:
        await state.clear()

    message = event if isinstance(event, types.Message) else event.message
    lang = await get_user_lang(event.from_user.id)
    page = 1

    if isinstance(event, types.CallbackQuery):
        await event.answer()
        if event.data.startswith("tpl_grid:"):
            parts = event.data.split(":", 1)
            if len(parts) > 1 and parts[1].isdigit():
                page = max(1, int(parts[1]))

    total_count = await get_templates_count()
    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    page = min(page, total_pages)

    offset = (page - 1) * PAGE_SIZE
    templates = await get_templates_paginated(limit=PAGE_SIZE, offset=offset)

    if not templates:
        empty_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("start.add_template", lang), callback_data="action_add_template")],
                [InlineKeyboardButton(text=t("catalog.search_public", lang), callback_data="menu_search")],
            ]
        )
        await message.answer(
            t("catalog.empty", lang),
            reply_markup=empty_kb,
        )
        return

    text = t("catalog.grid_title", lang).format(page=page, total_pages=total_pages, total=total_count)
    kb = build_catalog_grid_keyboard(templates, page, total_pages, lang=lang)

    if isinstance(event, types.CallbackQuery) and event.data.startswith("tpl_grid:"):
        try:
            await event.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass

    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "recent_grid")
async def handle_recent_grid(callback: types.CallbackQuery):
    """Show the user's recently used templates, reusing the catalog grid renderer."""
    await callback.answer()
    lang = await get_user_lang(callback.from_user.id)

    recent_ids: list = []
    if get_recent_templates is not None:
        try:
            recent_ids = await get_recent_templates(callback.from_user.id, limit=8) or []
        except Exception:
            recent_ids = []

    templates = []
    for tid in recent_ids:
        tpl = await get_template_by_id(tid)
        if tpl:
            templates.append(tpl)

    if not templates:
        await callback.answer(t("catalog.recent_empty", lang), show_alert=True)
        return

    text = t("catalog.recent_title", lang)
    kb = build_catalog_grid_keyboard(templates, 1, 1, lang=lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)

@router.message(Command("random"), StateFilter("*"), flags={"state": "*"})
async def handle_random_template_cmd(message: types.Message, state: FSMContext):
    """Fetch and present a random meme template, clearing any active state."""
    await state.clear()
    await _deliver_random_template(message.bot, message.chat.id, message.from_user.id)

async def _deliver_random_template(bot, chat_id: int, user_id: int) -> None:
    """Pick a random template and send it with the random-action keyboard."""
    lang = await get_user_lang(user_id)
    template = await get_random_template()
    if template:
        title = template.get("title") or template.get("name") or "Random Template"
        caption = t("catalog.random_caption", lang).format(
            title=title, tags=template.get("tags", "None")
        )
        is_admin_user = user_id in config.ADMIN_IDS
        kb = build_random_keyboard(template["id"], is_admin_user=is_admin_user, lang=lang)
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=template["file_id"],
                caption=caption,
                reply_markup=kb,
            )
            return
        except Exception:
            await bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb)
            return

    await bot.send_message(chat_id=chat_id, text=t("catalog.no_templates", lang))

@router.callback_query(F.data == "btn_random_again")
async def handle_random_again(callback: types.CallbackQuery):
    """Deliver a fresh random template as a new message."""
    await callback.answer()
    await _deliver_random_template(callback.message.bot, callback.message.chat.id, callback.from_user.id)

@router.callback_query(F.data.startswith("view_tpl:"))
async def handle_view_template(callback: types.CallbackQuery):
    """Display single template preview and actions."""
    lang = await get_user_lang(callback.from_user.id)
    parts = callback.data.split(":")
    template_id_str = parts[1] if len(parts) > 1 else ""
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    if not template_id_str.isdigit():
        await callback.answer(t("catalog.err_invalid_id", lang), show_alert=True)
        return

    template = await get_template_by_id(int(template_id_str))
    if not template:
        await callback.answer(t("catalog.err_not_found", lang), show_alert=True)
        return

    await callback.answer()
    is_admin_user = callback.from_user.id in config.ADMIN_IDS

    title = template.get("title") or template.get("name") or "Template"
    tags = template.get("tags") or "None"
    media_type = template.get("media_type") or "photo"
    uses = template.get("usage_count", 0)

    caption = t("catalog.detail_caption", lang).format(
        title=title, id=template["id"], media=media_type, uses=uses, tags=tags
    )

    kb = build_template_detail_keyboard(
        template["id"], page, is_admin_user=is_admin_user,
        is_fav=await is_favorite(callback.from_user.id, template["id"]),
        lang=lang,
    )

    try:
        await callback.message.answer_photo(
            photo=template["file_id"],
            caption=caption,
            reply_markup=kb,
        )
    except Exception:
        await callback.message.answer(caption, reply_markup=kb)

@router.callback_query(F.data.startswith("admin_del_tpl:"))
async def handle_admin_delete_template(callback: types.CallbackQuery):
    """Admin-only deletion of a template."""
    lang = await get_user_lang(callback.from_user.id)
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer(t("catalog.err_unauthorized", lang), show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await callback.answer(t("catalog.err_invalid_ref", lang), show_alert=True)
        return

    template_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    await delete_template(template_id)
    await callback.answer(t("catalog.tpl_deleted", lang))
    try:
        await callback.message.delete()
    except Exception:
        pass

# ------------------------------------------------------------------------------
# Favorites
# ------------------------------------------------------------------------------

FAV_PAGE_SIZE = 8

def build_favorites_keyboard(favorites: list, page: int, total_pages: int, lang: str = "bn") -> InlineKeyboardMarkup:
    """2-column grid of favorited templates with pagination; tap opens the detail view."""
    builder = InlineKeyboardBuilder()

    for tpl in favorites:
        title = tpl.get("title") or tpl.get("name") or "Template"
        display_label = title[:16] + ".." if len(title) > 18 else title
        builder.button(text=display_label, callback_data=f"view_tpl:{tpl['id']}:1")

    builder.adjust(2)

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text=t("catalog.prev", lang), callback_data=f"fav_page:{page - 1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text=t("catalog.next", lang), callback_data=f"fav_page:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text=t("misc.close", lang), callback_data="close_catalog"))

    return builder.as_markup()

async def _render_favorites_page(user_id: int, page: int) -> tuple:
    """Build (text, keyboard) for one page of the user's favorites list."""
    lang = await get_user_lang(user_id)
    favs = await get_favorites(user_id)
    total_pages = max(1, math.ceil(len(favs) / FAV_PAGE_SIZE)) if favs else 1
    page = min(max(1, page), total_pages)

    if not favs:
        text = t("catalog.fav_empty", lang)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=t("misc.close", lang), callback_data="close_catalog")]]
        )
        return text, kb

    chunk = favs[(page - 1) * FAV_PAGE_SIZE : page * FAV_PAGE_SIZE]
    text = t("catalog.fav_title", lang).format(page=page, total_pages=total_pages, total=len(favs))
    return text, build_favorites_keyboard(chunk, page, total_pages, lang=lang)

@router.callback_query(F.data.startswith("fav_toggle:"))
async def handle_fav_toggle(callback: types.CallbackQuery):
    """Toggle a template in/out of the user's favorites and refresh the button."""
    lang = await get_user_lang(callback.from_user.id)
    parts = callback.data.split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer(t("catalog.err_invalid_fav", lang), show_alert=True)
        return

    template_id = int(parts[1])
    page = int(parts[2])
    user_id = callback.from_user.id

    template = await get_template_by_id(template_id)
    if not template:
        await callback.answer(t("catalog.err_not_found", lang), show_alert=True)
        return

    if await is_favorite(user_id, template_id):
        await remove_favorite(user_id, template_id)
        new_fav = False
        await callback.answer(t("catalog.fav_removed", lang))
    else:
        await add_favorite(user_id, template_id)
        new_fav = True
        await callback.answer(t("catalog.fav_added", lang))

    kb = build_template_detail_keyboard(
        template_id, page,
        is_admin_user=user_id in config.ADMIN_IDS,
        is_fav=new_fav,
        lang=lang,
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass

@router.message(Command("favorites"), StateFilter("*"))
async def handle_favorites_command(message: types.Message, state: Optional[FSMContext] = None):
    """List the user's favorited templates across any state."""
    if state:
        await state.clear()
    text, kb = await _render_favorites_page(message.from_user.id, 1)
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("fav_page:"))
async def handle_fav_page(callback: types.CallbackQuery):
    """Paginate the favorites list in place."""
    parts = callback.data.split(":", 1)
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    await callback.answer()
    text, kb = await _render_favorites_page(callback.from_user.id, page)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
