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
    is_favorite,
    add_favorite,
    remove_favorite,
    get_favorites,
)

router = Router(name="catalog_router")

PAGE_SIZE = 6

def build_catalog_grid_keyboard(templates: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Build multi-column grid keyboard (2 items per row) with pagination controls."""
    builder = InlineKeyboardBuilder()

    # 1. Add template buttons (2 per row)
    for t in templates:
        title = t.get("title") or t.get("name") or "Template"
        display_label = title[:16] + ".." if len(title) > 18 else title
        builder.button(text=f"[{display_label}]", callback_data=f"view_tpl:{t['id']}:{page}")

    builder.adjust(2)

    # 2. Row 1: Pagination controls
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="[<< Prev]", callback_data=f"tpl_grid:{page - 1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text="[|<<]", callback_data="noop"))

    nav_buttons.append(InlineKeyboardButton(text=f"[Page {page}/{total_pages}]", callback_data="noop"))

    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="[Next >>]", callback_data=f"tpl_grid:{page + 1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text="[>>|]", callback_data="noop"))

    builder.row(*nav_buttons)

    # 3. Row 2: Secondary actions
    builder.row(
        InlineKeyboardButton(text="[+ Add Template]", callback_data="action_add_template"),
        InlineKeyboardButton(text="[Search]", callback_data="menu_search"),
        InlineKeyboardButton(text="[Close]", callback_data="close_catalog"),
    )

    return builder.as_markup()

def build_template_detail_keyboard(template_id: int, page: int, is_admin_user: bool = False, is_fav: bool = False) -> InlineKeyboardMarkup:
    """Action buttons for template detail view."""
    buttons = [
        [
            InlineKeyboardButton(text="[Create Meme]", callback_data=f"btn_create:{template_id}"),
            InlineKeyboardButton(text="[Download Raw]", callback_data=f"btn_raw:{template_id}"),
        ],
        [
            InlineKeyboardButton(
                text="[Remove from Favorites]" if is_fav else "[Add to Favorites]",
                callback_data=f"fav_toggle:{template_id}:{page}",
            ),
        ],
        [
            InlineKeyboardButton(text="[Watermark Settings]", callback_data="menu_settings"),
            InlineKeyboardButton(text="[Back to Grid]", callback_data=f"tpl_grid:{page}"),
        ],
    ]

    if is_admin_user:
        buttons.append([InlineKeyboardButton(text="[Delete Template]", callback_data=f"admin_del_tpl:{template_id}:{page}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_random_keyboard(template_id: int, is_admin_user: bool = False) -> InlineKeyboardMarkup:
    """Action buttons for a delivered random template, including a re-roll button."""
    buttons = [
        [
            InlineKeyboardButton(text="[Create Meme]", callback_data=f"btn_create:{template_id}"),
            InlineKeyboardButton(text="[Download Raw]", callback_data=f"btn_raw:{template_id}"),
        ],
        [
            InlineKeyboardButton(text="[Another Random]", callback_data="btn_random_again"),
        ],
    ]

    if is_admin_user:
        buttons.append([InlineKeyboardButton(text="[Delete Template]", callback_data=f"admin_del_tpl:{template_id}:1")])

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
                [InlineKeyboardButton(text="[+ Add Template]", callback_data="action_add_template")],
                [InlineKeyboardButton(text="[Search Public Memes]", callback_data="menu_search")],
            ]
        )
        await message.answer(
            "[Catalog is currently empty.]\n"
            "Upload templates using [+ Add Template] or search public meme repositories.",
            reply_markup=empty_kb,
        )
        return

    text = f"[Meme Template Catalog - Page {page}/{total_pages} (Total: {total_count})]\nSelect a template to view details or create a meme:"
    kb = build_catalog_grid_keyboard(templates, page, total_pages)

    if isinstance(event, types.CallbackQuery) and event.data.startswith("tpl_grid:"):
        try:
            await event.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass

    await message.answer(text, reply_markup=kb)

@router.message(Command("random"), StateFilter("*"), flags={"state": "*"})
async def handle_random_template_cmd(message: types.Message, state: FSMContext):
    """Fetch and present a random meme template, clearing any active state."""
    await state.clear()
    await _deliver_random_template(message.bot, message.chat.id, message.from_user.id)

async def _deliver_random_template(bot, chat_id: int, user_id: int) -> None:
    """Pick a random template and send it with the random-action keyboard."""
    template = await get_random_template()
    if template:
        title = template.get("title") or template.get("name") or "Random Template"
        caption = (
            f"<b>[RANDOM TEMPLATE: {title}]</b>\n"
            f"Tags: {template.get('tags', 'None')}"
        )
        is_admin_user = user_id in config.ADMIN_IDS
        kb = build_random_keyboard(template["id"], is_admin_user=is_admin_user)
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=template["file_id"],
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML",
            )
            return
        except Exception:
            await bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb, parse_mode="HTML")
            return

    await bot.send_message(
        chat_id=chat_id,
        text="[Info: No templates currently available. Upload one using /add_template]",
    )

@router.callback_query(F.data == "btn_random_again")
async def handle_random_again(callback: types.CallbackQuery):
    """Deliver a fresh random template as a new message."""
    await callback.answer()
    await _deliver_random_template(callback.message.bot, callback.message.chat.id, callback.from_user.id)

@router.callback_query(F.data.startswith("view_tpl:"))
async def handle_view_template(callback: types.CallbackQuery):
    """Display single template preview and actions."""
    parts = callback.data.split(":")
    template_id_str = parts[1] if len(parts) > 1 else ""
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    if not template_id_str.isdigit():
        await callback.answer("[Error: Invalid template ID.]", show_alert=True)
        return

    template = await get_template_by_id(int(template_id_str))
    if not template:
        await callback.answer("[Error: Template not found.]", show_alert=True)
        return

    await callback.answer()
    is_admin_user = callback.from_user.id in config.ADMIN_IDS

    title = template.get("title") or template.get("name") or "Template"
    tags = template.get("tags") or "None"
    media_type = template.get("media_type") or "photo"
    uses = template.get("usage_count", 0)

    caption = (
        f"[Template Details]\n"
        f"Title: {title}\n"
        f"ID: #{template['id']}\n"
        f"Media: {media_type}\n"
        f"Usage: {uses} times\n"
        f"Tags: {tags}"
    )

    kb = build_template_detail_keyboard(
        template["id"], page, is_admin_user=is_admin_user,
        is_fav=await is_favorite(callback.from_user.id, template["id"]),
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
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("[Unauthorized action.]", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await callback.answer("[Error: Invalid template reference.]", show_alert=True)
        return

    template_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    await delete_template(template_id)
    await callback.answer("[Template deleted successfully.]")
    try:
        await callback.message.delete()
    except Exception:
        pass

# ------------------------------------------------------------------------------
# Favorites
# ------------------------------------------------------------------------------

FAV_PAGE_SIZE = 8

def build_favorites_keyboard(favorites: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """2-column grid of favorited templates with pagination; tap opens the detail view."""
    builder = InlineKeyboardBuilder()

    for t in favorites:
        title = t.get("title") or t.get("name") or "Template"
        display_label = title[:16] + ".." if len(title) > 18 else title
        builder.button(text=f"[{display_label}]", callback_data=f"view_tpl:{t['id']}:1")

    builder.adjust(2)

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="[<< Prev]", callback_data=f"fav_page:{page - 1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="[Next >>]", callback_data=f"fav_page:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="[Close]", callback_data="close_catalog"))

    return builder.as_markup()

async def _render_favorites_page(user_id: int, page: int) -> tuple:
    """Build (text, keyboard) for one page of the user's favorites list."""
    favs = await get_favorites(user_id)
    total_pages = max(1, math.ceil(len(favs) / FAV_PAGE_SIZE)) if favs else 1
    page = min(max(1, page), total_pages)

    if not favs:
        text = "[No favorites yet. Tap [Add to Favorites] on any template.]"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="[Close]", callback_data="close_catalog")]]
        )
        return text, kb

    chunk = favs[(page - 1) * FAV_PAGE_SIZE : page * FAV_PAGE_SIZE]
    text = f"[My Favorites - Page {page}/{total_pages} (Total: {len(favs)})]"
    return text, build_favorites_keyboard(chunk, page, total_pages)

@router.callback_query(F.data.startswith("fav_toggle:"))
async def handle_fav_toggle(callback: types.CallbackQuery):
    """Toggle a template in/out of the user's favorites and refresh the button."""
    parts = callback.data.split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("[Error: Invalid favorite reference.]", show_alert=True)
        return

    template_id = int(parts[1])
    page = int(parts[2])
    user_id = callback.from_user.id

    template = await get_template_by_id(template_id)
    if not template:
        await callback.answer("[Error: Template not found.]", show_alert=True)
        return

    if await is_favorite(user_id, template_id):
        await remove_favorite(user_id, template_id)
        new_fav = False
        await callback.answer("[Removed from favorites.]")
    else:
        await add_favorite(user_id, template_id)
        new_fav = True
        await callback.answer("[Added to favorites.]")

    kb = build_template_detail_keyboard(
        template_id, page,
        is_admin_user=user_id in config.ADMIN_IDS,
        is_fav=new_fav,
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
