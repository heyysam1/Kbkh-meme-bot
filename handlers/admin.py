import re
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from database.queries import (
    add_template,
    add_banner,
    get_all_banners,
    delete_banner,
    add_alias,
    get_all_aliases,
    get_all_templates,
    add_source,
    get_all_sources,
    remove_source,
)

router = Router(name="admin_router")

def is_admin(user_id: int) -> bool:
    """Check if user has administrative rights."""
    if not config.ADMIN_IDS:
        return False  # Secure fail-closed: if no admin IDs configured, deny all admin actions
    return user_id in config.ADMIN_IDS

# ------------------------------------------------------------------------------
# Admin Dashboard
# ------------------------------------------------------------------------------

@router.message(Command("admin"))
async def handle_admin_dashboard(message: types.Message):
    """Display admin controls and operational overview."""
    if not is_admin(message.from_user.id):
        await message.answer("[Access Denied: Admin privileges required.]")
        return

    admin_text = (
        "<b>[ADMIN CONTROL PANEL]</b>\n\n"
        "<b>Template Ingestion:</b>\n"
        "• Send or forward media (photo/video/gif) directly to this chat.\n"
        "• Use /add_template to launch the submission workflow.\n\n"
        "<b>External Sources:</b>\n"
        "• /add_source &lt;url&gt; [name] - Register new meme repository/feed\n"
        "• /sources - View and manage configured sources\n"
        "• /remove_source &lt;id&gt; - Remove external source by ID\n\n"
        "<b>Banners & Aliases:</b>\n"
        "• /addbanner &lt;title&gt; - Register promotional banner (photo attached/reply)\n"
        "• /banners - List all banners with delete controls\n"
        "• /addalias &lt;term&gt; = &lt;title&gt; - Map alias to official template\n"
        "• /aliases - List registered alias mappings\n\n"
        "<b>System Metrics:</b>\n"
        "• /stats - Display real-time database counts and usage metrics"
    )
    await message.answer(admin_text, parse_mode="HTML")

# ------------------------------------------------------------------------------
# Admin Retroactive Bulk Ingestion (Forwarded Channel Posts & Direct Uploads)
# ------------------------------------------------------------------------------

@router.message(F.chat.type == "private", F.photo | F.document | F.video | F.animation)
async def handle_admin_retroactive_ingest(message: types.Message):
    """
    Allow admins to forward past channel posts (photos, documents, videos, animations)
    directly to the bot's private chat to populate the template catalog retroactively.
    """
    if not is_admin(message.from_user.id):
        return

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

    channel_info = f" (Channel: <i>{source_channel_title}</i>)" if source_channel_title else ""
    await message.reply(
        f"[Success: Template ingested]\n\n"
        f"• ID: <code>#{template_id}</code>\n"
        f"• Title: {title}\n"
        f"• Media Type: <code>{media_type}</code>\n"
        f"• Tags: <code>{tags_str or 'None'}</code>{channel_info}",
        parse_mode="HTML",
    )

# ------------------------------------------------------------------------------
# External Source Management (/add_source, /sources, /remove_source)
# ------------------------------------------------------------------------------

@router.message(Command("add_source"))
async def handle_add_source_command(message: types.Message):
    """
    Register an external meme feed or API source.
    Usage: /add_source <url> [name]
    """
    if not is_admin(message.from_user.id):
        await message.answer("[Access Denied: Admin privileges required.]")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "[Error: Missing arguments]\n"
            "Usage: /add_source <url> [name]\n"
            "Example: /add_source https://api.imgflip.com/get_memes Imgflip API"
        )
        return

    url = parts[1].strip()
    name = parts[2].strip() if len(parts) > 2 else url

    source_id = await add_source(url=url, name=name)
    await message.answer(
        f"[Success: Source registered]\n\n"
        f"• ID: <code>#{source_id}</code>\n"
        f"• Name: {name}\n"
        f"• URL: <code>{url}</code>",
        parse_mode="HTML",
    )

@router.message(Command("sources"))
async def handle_list_sources_command(message: types.Message):
    """List all registered external meme sources."""
    if not is_admin(message.from_user.id):
        return

    sources = await get_all_sources()
    if not sources:
        await message.answer(
            "[Info: No external sources configured. Use /add_source <url> [name] to add one.]"
        )
        return

    lines = ["<b>[CONFIGURED EXTERNAL SOURCES]</b>\n"]
    kb_rows = []
    for s in sources:
        lines.append(f"• ID <code>{s['id']}</code> | <b>{s['name']}</b>\n  <code>{s['url']}</code>")
        kb_rows.append([
            InlineKeyboardButton(
                text=f"[Remove #{s['id']}]",
                callback_data=f"cb_del_source:{s['id']}",
            )
        ])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer("\n".join(lines), reply_markup=kb, parse_mode="HTML")

@router.message(Command("remove_source"))
async def handle_remove_source_command(message: types.Message):
    """
    Remove an external meme feed source.
    Usage: /remove_source <id>
    """
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("[Error: Provide a valid source ID. Example: /remove_source 1]")
        return

    source_id = int(parts[1].strip())
    await remove_source(source_id)
    await message.answer(f"[Success: Source #{source_id} removed.]")

@router.callback_query(F.data.startswith("cb_del_source:"))
async def handle_delete_source_callback(callback: types.CallbackQuery):
    """Handle inline button deletion of an external source."""
    if not is_admin(callback.from_user.id):
        await callback.answer("[Access Denied]", show_alert=True)
        return

    source_id_str = callback.data.split(":", 1)[1]
    if source_id_str.isdigit():
        await remove_source(int(source_id_str))
        await callback.answer("[Source removed]")
        # Refresh the list
        sources = await get_all_sources()
        if not sources:
            await callback.message.edit_text(
                "[Info: No external sources configured. Use /add_source <url> [name] to add one.]"
            )
            return

        lines = ["<b>[CONFIGURED EXTERNAL SOURCES]</b>\n"]
        kb_rows = []
        for s in sources:
            lines.append(f"• ID <code>{s['id']}</code> | <b>{s['name']}</b>\n  <code>{s['url']}</code>")
            kb_rows.append([
                InlineKeyboardButton(
                    text=f"[Remove #{s['id']}]",
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

@router.message(Command("addbanner"))
async def handle_add_banner_command(message: types.Message):
    """
    Add a promotional banner to the library:
    Usage: Send with a photo or reply to a photo: /addbanner <Banner Title>
    """
    if not is_admin(message.from_user.id):
        await message.answer("[Access Denied: Admin privileges required.]")
        return

    parts = message.text.split(maxsplit=1)
    banner_name = parts[1].strip() if len(parts) > 1 else "KBKH Promo Banner"

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.reply_to_message and message.reply_to_message.photo:
        file_id = message.reply_to_message.photo[-1].file_id

    if not file_id:
        await message.answer(
            "[Error: Banner image not found]\n\n"
            "Usage:\n"
            "• Attach photo with caption: /addbanner <name>\n"
            "• Or reply to a photo message with: /addbanner <name>"
        )
        return

    banner_id = await add_banner(name=banner_name, file_id=file_id, is_default=0)
    await message.answer(
        f"[Success: Banner registered]\n\n"
        f"• Banner ID: <code>{banner_id}</code>\n"
        f"• Name: {banner_name}",
        parse_mode="HTML",
    )

@router.message(Command("banners"))
async def handle_list_banners_admin(message: types.Message):
    """List all registered promotional banners with deletion controls."""
    if not is_admin(message.from_user.id):
        return

    banners = await get_all_banners()
    if not banners:
        await message.answer("[Info: No promotional banners found. Add with /addbanner]")
        return

    await message.answer("<b>[REGISTERED PROMOTIONAL BANNERS]</b>", parse_mode="HTML")

    for b in banners:
        del_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="[Delete]", callback_data=f"cb_del_banner:{b['id']}")]]
        )
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=b["file_id"],
                caption=f"[Banner] {b['name']} (ID: {b['id']})",
                reply_markup=del_kb,
            )
        except Exception:
            await message.answer(
                f"[Banner] {b['name']} (ID: {b['id']})",
                reply_markup=del_kb,
            )

@router.callback_query(F.data.startswith("cb_del_banner:"))
async def handle_delete_banner(callback: types.CallbackQuery):
    """Delete promotional banner from library."""
    if not is_admin(callback.from_user.id):
        await callback.answer("[Access Denied]", show_alert=True)
        return

    banner_id = callback.data.split(":", 1)[1]
    if banner_id.isdigit():
        await delete_banner(int(banner_id))
        await callback.answer("[Banner deleted]")
        await callback.message.delete()

# ------------------------------------------------------------------------------
# Alias Management
# ------------------------------------------------------------------------------

@router.message(Command("addalias"))
async def handle_add_alias_command(message: types.Message):
    """
    Register a search alias mapping:
    Usage: /addalias <informal term> = <canonical template title>
    """
    if not is_admin(message.from_user.id):
        return

    payload = message.text[len("/addalias") :].strip()
    if "=" not in payload:
        await message.answer(
            "[Error: Invalid format]\n"
            "Format: /addalias <term> = <official title>\n"
            "Example: /addalias tony stark stare = Robert Downey Jr Eye Roll"
        )
        return

    term, canonical = payload.split("=", 1)
    term = term.strip().lower()
    canonical = canonical.strip()

    if not term or not canonical:
        await message.answer("[Error: Both alias term and official name are required.]")
        return

    await add_alias(term, canonical)
    await message.answer(
        f"[Success: Alias registered]\n\n"
        f"• Search Term: <code>{term}</code>\n"
        f"• Canonical Title: <b>{canonical}</b>",
        parse_mode="HTML",
    )

@router.message(Command("aliases"))
async def handle_list_aliases_command(message: types.Message):
    """Display registered alias mappings."""
    if not is_admin(message.from_user.id):
        return

    aliases = await get_all_aliases()
    if not aliases:
        await message.answer("[Info: No alias mappings registered.]")
        return

    lines = ["<b>[REGISTERED SEARCH ALIASES]</b>\n"]
    for a in aliases:
        lines.append(f"• <code>{a['alias_term']}</code> -&gt; <b>{a['canonical_name']}</b>")

    await message.answer("\n".join(lines), parse_mode="HTML")

# ------------------------------------------------------------------------------
# Admin Statistics
# ------------------------------------------------------------------------------

@router.message(Command("stats"))
async def handle_admin_stats(message: types.Message):
    """Show operational statistics."""
    if not is_admin(message.from_user.id):
        return

    templates = await get_all_templates(limit=1000)
    banners = await get_all_banners()
    aliases = await get_all_aliases()
    sources = await get_all_sources()

    total_templates = len(templates)
    total_banners = len(banners)
    total_aliases = len(aliases)
    total_sources = len(sources)
    total_uses = sum(t.get("usage_count", 0) for t in templates)

    stats_text = (
        f"<b>[SYSTEM STATISTICS]</b>\n\n"
        f"• Total Templates: {total_templates}\n"
        f"• Total Memes Rendered: {total_uses}\n"
        f"• Registered Banners: {total_banners}\n"
        f"• Search Aliases: {total_aliases}\n"
        f"• External Sources: {total_sources}"
    )
    await message.answer(stats_text, parse_mode="HTML")
