import re
from typing import Optional
from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from database.queries import (
    add_template,
    add_banner,
    get_all_banners,
    delete_banner,
    add_alias,
    get_all_aliases,
    delete_alias,
    get_all_templates,
    add_source,
    get_all_sources,
    remove_source,
    get_user_lang,
)
from services.i18n import t

router = Router(name="admin_router")

class BannerSG(StatesGroup):
    waiting_for_photo = State()

def is_admin(user_id: int) -> bool:
    """Check if user has administrative rights."""
    if not config.ADMIN_IDS:
        return False  # Secure fail-closed: if no admin IDs configured, deny all admin actions
    return user_id in config.ADMIN_IDS

# ------------------------------------------------------------------------------
# Admin Dashboard
# ------------------------------------------------------------------------------

@router.message(Command("admin"), StateFilter("*"), flags={"state": "*"})
async def handle_admin_dashboard(message: types.Message, state: Optional[FSMContext] = None):
    """Display admin controls and operational overview, clearing any active state."""
    if state:
        await state.clear()
    lang = await get_user_lang(message.from_user.id)
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.access_denied", lang))
        return

    await message.answer(t("admin.panel", lang), parse_mode="HTML")

# ------------------------------------------------------------------------------
# Admin Retroactive Bulk Ingestion (Forwarded Channel Posts & Direct Uploads)
# ------------------------------------------------------------------------------

@router.message(StateFilter(None), F.chat.type == "private", F.photo | F.document | F.video | F.animation)
async def handle_admin_retroactive_ingest(message: types.Message):
    """
    Allow admins to forward past channel posts (photos, documents, videos, animations)
    directly to the bot's private chat to populate the template catalog retroactively.
    """
    if not is_admin(message.from_user.id):
        return
    lang = await get_user_lang(message.from_user.id)

    from handlers.channel import extract_template_metadata
    meta = extract_template_metadata(message)
    if not meta:
        return

    file_id, file_unique_id, media_type, title, tags_str = meta

    # Extract origin channel info if forwarded
    source_channel_id = None
    source_channel_title = None

    if hasattr(message, "forward_origin") and message.forward_origin:
        origin = message.forward_origin
        if getattr(origin, "type", "") == "channel" and getattr(origin, "chat", None):
            source_channel_id = str(origin.chat.id)
            source_channel_title = origin.chat.title
    elif getattr(message, "forward_from_chat", None):
        source_channel_id = str(message.forward_from_chat.id)
        source_channel_title = message.forward_from_chat.title

    template_id = await add_template(
        file_id=file_id,
        file_unique_id=file_unique_id,
        media_type=media_type,
        title=title,
        name=title,
        tags=tags_str,
        source_channel_id=source_channel_id,
        source_channel_title=source_channel_title,
        is_trending=1 if "trending" in tags_str.lower() else 0,
        added_by=message.from_user.id,
    )

    channel_info = (
        t("admin.from_channel", lang).format(name=source_channel_title)
        if source_channel_title else ""
    )
    await message.reply(
        t("admin.template_ingested", lang).format(
            id=template_id,
            title=title,
            media_type=media_type,
            tags=tags_str or "-",
            channel=channel_info,
        )
    )

# ------------------------------------------------------------------------------
# External Source Management (/add_source, /sources, /remove_source)
# ------------------------------------------------------------------------------

@router.message(Command("add_source"), StateFilter("*"), flags={"state": "*"})
async def handle_add_source_command(message: types.Message, state: Optional[FSMContext] = None):
    """Register an external meme feed or API source across any state."""
    if state:
        await state.clear()
    lang = await get_user_lang(message.from_user.id)
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.access_denied", lang))
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(t("admin.add_source_usage", lang))
        return

    url = parts[1].strip()
    name = parts[2].strip() if len(parts) > 2 else url

    source_id = await add_source(url=url, name=name)
    await message.answer(
        t("admin.source_added", lang).format(id=source_id, name=name, url=url),
        parse_mode="HTML",
    )

@router.message(Command("sources"), StateFilter("*"), flags={"state": "*"})
async def handle_list_sources_command(message: types.Message, state: Optional[FSMContext] = None):
    """List all registered external meme sources across any state."""
    if state:
        await state.clear()
    if not is_admin(message.from_user.id):
        return
    lang = await get_user_lang(message.from_user.id)

    sources = await get_all_sources()
    if not sources:
        await message.answer(t("admin.no_sources", lang))
        return

    lines = [t("admin.sources_title", lang) + "\n"]
    kb_rows = []
    for s in sources:
        lines.append(
            t("admin.source_row", lang).format(id=s["id"], name=s["name"], url=s["url"])
        )
        kb_rows.append([
            InlineKeyboardButton(
                text=t("admin.remove_btn", lang),
                callback_data=f"cb_del_source:{s['id']}",
            )
        ])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer("\n".join(lines), reply_markup=kb, parse_mode="HTML")

@router.message(Command("remove_source"), StateFilter("*"), flags={"state": "*"})
async def handle_remove_source_command(message: types.Message, state: Optional[FSMContext] = None):
    """Remove an external meme feed source across any state."""
    if state:
        await state.clear()
    lang = await get_user_lang(message.from_user.id)
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.access_denied", lang))
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer(t("admin.remove_source_usage", lang))
        return

    source_id = int(parts[1].strip())
    await remove_source(source_id)
    await message.answer(t("admin.source_removed", lang).format(id=source_id))

@router.callback_query(F.data.startswith("cb_del_source:"))
async def handle_delete_source_callback(callback: types.CallbackQuery):
    """Handle inline button deletion of an external source."""
    lang = await get_user_lang(callback.from_user.id)
    if not is_admin(callback.from_user.id):
        await callback.answer(t("admin.access_denied", lang), show_alert=True)
        return

    source_id_str = callback.data.split(":", 1)[1]
    if not source_id_str.isdigit():
        await callback.answer(t("admin.invalid_source_id", lang), show_alert=True)
        return

    await remove_source(int(source_id_str))
    await callback.answer(t("admin.source_removed_toast", lang))
    # Refresh the list
    sources = await get_all_sources()
    if not sources:
        await callback.message.edit_text(t("admin.no_sources", lang))
        return

    lines = [t("admin.sources_title", lang) + "\n"]
    kb_rows = []
    for s in sources:
        lines.append(
            t("admin.source_row", lang).format(id=s["id"], name=s["name"], url=s["url"])
        )
        kb_rows.append([
            InlineKeyboardButton(
                text=t("admin.remove_btn", lang),
                callback_data=f"cb_del_source:{s['id']}",
            )
        ])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )

# ------------------------------------------------------------------------------
# Banner Management
# ------------------------------------------------------------------------------

@router.message(Command("addbanner"), StateFilter("*"), flags={"state": "*"})
async def handle_add_banner_command(message: types.Message, state: Optional[FSMContext] = None):
    """Add a promotional banner across any state."""
    if state:
        await state.clear()
    lang = await get_user_lang(message.from_user.id)
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.access_denied", lang))
        return

    # Caption posts carry the command in message.caption, not message.text.
    raw_text = message.text or message.caption or ""
    parts = raw_text.split(maxsplit=1)
    banner_name = parts[1].strip() if len(parts) > 1 else "KBKH Promo Banner"

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.reply_to_message and message.reply_to_message.photo:
        file_id = message.reply_to_message.photo[-1].file_id

    if not file_id:
        # No photo attached: ask for one and wait. The next photo sent
        # becomes the banner (caption used as its name, if given).
        await state.set_state(BannerSG.waiting_for_photo)
        await message.answer(t("admin.banner_upload_prompt", lang))
        return

    banner_id = await add_banner(name=banner_name, file_id=file_id, is_default=0)
    await message.answer(
        t("admin.banner_added", lang).format(id=banner_id, name=banner_name),
        parse_mode="HTML",
    )

@router.message(BannerSG.waiting_for_photo, F.photo)
async def handle_banner_photo_upload(message: types.Message, state: FSMContext):
    """Save the photo sent after /addbanner as a new banner."""
    lang = await get_user_lang(message.from_user.id)
    if not is_admin(message.from_user.id):
        await state.clear()
        await message.answer(t("admin.access_denied", lang))
        return
    banner_name = (message.caption or "").strip() or "KBKH Promo Banner"
    banner_id = await add_banner(
        name=banner_name, file_id=message.photo[-1].file_id, is_default=0
    )
    await state.clear()
    await message.answer(
        t("admin.banner_added", lang).format(id=banner_id, name=banner_name),
        parse_mode="HTML",
    )

@router.message(BannerSG.waiting_for_photo, Command("cancel"))
async def handle_banner_upload_cancel(message: types.Message, state: FSMContext):
    lang = await get_user_lang(message.from_user.id)
    await state.clear()
    await message.answer(t("common.cancel", lang))

@router.message(Command("banners"), StateFilter("*"), flags={"state": "*"})
async def handle_list_banners_admin(message: types.Message, state: Optional[FSMContext] = None):
    """List all registered promotional banners across any state."""
    if state:
        await state.clear()
    if not is_admin(message.from_user.id):
        return
    lang = await get_user_lang(message.from_user.id)

    banners = await get_all_banners()
    if not banners:
        await message.answer(t("admin.no_banners", lang))
        return

    await message.answer(t("admin.banners_title", lang), parse_mode="HTML")

    for b in banners:
        del_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(
                text=t("common.delete", lang),
                callback_data=f"cb_del_banner:{b['id']}",
            )]]
        )
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=b["file_id"],
                caption=t("admin.banner_caption", lang).format(name=b["name"], id=b["id"]),
                reply_markup=del_kb,
            )
        except Exception:
            await message.answer(
                t("admin.banner_caption", lang).format(name=b["name"], id=b["id"]),
                reply_markup=del_kb,
            )

@router.callback_query(F.data.startswith("cb_del_banner:"))
async def handle_delete_banner(callback: types.CallbackQuery):
    """Delete promotional banner from library."""
    lang = await get_user_lang(callback.from_user.id)
    if not is_admin(callback.from_user.id):
        await callback.answer(t("admin.access_denied", lang), show_alert=True)
        return

    banner_id = callback.data.split(":", 1)[1]
    if banner_id.isdigit():
        await delete_banner(int(banner_id))
        await callback.answer(t("admin.banner_deleted_toast", lang))
        await callback.message.delete()

# ------------------------------------------------------------------------------
# Alias Management
# ------------------------------------------------------------------------------

@router.message(Command("addalias"), StateFilter("*"), flags={"state": "*"})
async def handle_add_alias_command(message: types.Message, state: Optional[FSMContext] = None):
    """Register an alias mapping across any state."""
    if state:
        await state.clear()
    lang = await get_user_lang(message.from_user.id)
    if not is_admin(message.from_user.id):
        return

    payload = (message.text or "")[len("/addalias"):].strip()
    if "=" not in payload:
        await message.answer(t("admin.addalias_usage", lang))
        return

    term, canonical = payload.split("=", 1)
    term = term.strip().lower()
    canonical = canonical.strip()

    if not term or not canonical:
        await message.answer(t("admin.alias_both_required", lang))
        return

    await add_alias(term, canonical)
    await message.answer(
        t("admin.alias_added", lang).format(term=term, canonical=canonical),
        parse_mode="HTML",
    )

@router.message(Command("aliases"), StateFilter("*"), flags={"state": "*"})
async def handle_list_aliases_command(message: types.Message, state: Optional[FSMContext] = None):
    """Display registered alias mappings across any state."""
    if state:
        await state.clear()
    if not is_admin(message.from_user.id):
        return
    lang = await get_user_lang(message.from_user.id)

    aliases = await get_all_aliases()
    if not aliases:
        await message.answer(t("admin.no_aliases", lang))
        return

    lines = [t("admin.aliases_title", lang) + "\n"]
    for a in aliases:
        lines.append(
            t("admin.alias_row", lang).format(
                term=a["alias_term"], canonical=a["canonical_name"]
            )
        )

    await message.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("delalias"), StateFilter("*"), flags={"state": "*"})
async def handle_delete_alias_command(message: types.Message, state: Optional[FSMContext] = None):
    """Delete a registered alias mapping across any state."""
    if state:
        await state.clear()
    lang = await get_user_lang(message.from_user.id)
    if not is_admin(message.from_user.id):
        return

    term = (message.text or "")[len("/delalias"):].strip()
    if not term:
        await message.answer(t("admin.delalias_usage", lang))
        return

    if await delete_alias(term):
        await message.answer(
            t("admin.alias_deleted", lang).format(term=term), parse_mode="HTML"
        )
    else:
        await message.answer(
            t("admin.alias_not_found", lang).format(term=term), parse_mode="HTML"
        )

# ------------------------------------------------------------------------------
# Admin Statistics
# ------------------------------------------------------------------------------

@router.message(Command("stats"), StateFilter("*"), flags={"state": "*"})
async def handle_admin_stats(message: types.Message, state: Optional[FSMContext] = None):
    """Show operational statistics across any state."""
    if state:
        await state.clear()
    if not is_admin(message.from_user.id):
        return
    lang = await get_user_lang(message.from_user.id)

    templates = await get_all_templates(limit=1000)
    banners = await get_all_banners()
    aliases = await get_all_aliases()
    sources = await get_all_sources()

    total_templates = len(templates)
    total_banners = len(banners)
    total_aliases = len(aliases)
    total_sources = len(sources)
    total_uses = sum(tpl.get("usage_count", 0) for tpl in templates)

    await message.answer(
        t("admin.stats", lang).format(
            templates=total_templates,
            uses=total_uses,
            banners=total_banners,
            aliases=total_aliases,
            sources=total_sources,
        ),
        parse_mode="HTML",
    )
