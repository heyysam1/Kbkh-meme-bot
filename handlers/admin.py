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
)

router = Router(name="admin_router")

def is_admin(user_id: int) -> bool:
    """Check if user has administrative rights."""
    if not config.ADMIN_IDS:
        return False  # Secure fail-closed: if no admin IDs configured, deny all admin actions
    return user_id in config.ADMIN_IDS

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
    )

    channel_info = f" (চ্যানেল: <i>{source_channel_title}</i>)" if source_channel_title else ""
    await message.reply(
        f"✅ <b>টেমপ্লেট সফলভাবে ইনজেস্ট করা হয়েছে!</b>\n\n"
        f"• <b>আইডি:</b> <code>#{template_id}</code>\n"
        f"• <b>টাইটেল:</b> {title}\n"
        f"• <b>মিডিয়া টাইপ:</b> <code>{media_type}</code>\n"
        f"• <b>ট্যাগ:</b> <code>{tags_str or 'None'}</code>{channel_info}",
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
        await message.answer("⛔ এই কমান্ডটি শুধুমাত্র অ্যাডমিনদের জন্য।")
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
            "❌ <b>ব্যানারের ছবি পাওয়া যায়নি!</b>\n\n"
            "ব্যবহারের নিয়ম:\n"
            "• ব্যানারের ছবির সাথে ক্যাপশনে লিখুন: <code>/addbanner &lt;ব্যানারের নাম&gt;</code>\n"
            "• অথবা ব্যানারের ছবিটিকে রিপ্লাই করে কমান্ডটি লিখুন।",
            parse_mode="HTML",
        )
        return

    banner_id = await add_banner(name=banner_name, file_id=file_id, is_default=0)
    await message.answer(
        f"✅ <b>প্রমোশনাল ব্যানার সফলভাবে সংরক্ষিত হয়েছে!</b>\n\n"
        f"• <b>ব্যানার আইডি:</b> <code>{banner_id}</code>\n"
        f"• <b>নাম:</b> {banner_name}",
        parse_mode="HTML",
    )

@router.message(Command("banners"))
async def handle_list_banners_admin(message: types.Message):
    """List all registered promotional banners with deletion buttons."""
    if not is_admin(message.from_user.id):
        return

    banners = await get_all_banners()
    if not banners:
        await message.answer("⚠️ বর্তমানে কোনো ব্যানার যুক্ত করা নেই। <code>/addbanner</code> দিয়ে যুক্ত করুন।", parse_mode="HTML")
        return

    await message.answer("📢 <b>সংরক্ষিত প্রমোশনাল ব্যানার তালিকা:</b>", parse_mode="HTML")

    for b in banners:
        del_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🗑️ ডিলিট করুন", callback_data=f"cb_del_banner:{b['id']}")]]
        )
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id,
                photo=b["file_id"],
                caption=f"📢 <b>{b['name']}</b> (ID: {b['id']})",
                reply_markup=del_kb,
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(
                f"📢 <b>{b['name']}</b> (ID: {b['id']})",
                reply_markup=del_kb,
                parse_mode="HTML",
            )

@router.callback_query(F.data.startswith("cb_del_banner:"))
async def handle_delete_banner(callback: types.CallbackQuery):
    """Delete promotional banner from library."""
    if not is_admin(callback.from_user.id):
        await callback.answer("অননুমোদিত অ্যাকশন।", show_alert=True)
        return

    banner_id = callback.data.split(":", 1)[1]
    if banner_id.isdigit():
        await delete_banner(int(banner_id))
        await callback.answer("✅ ব্যানার ডিলিট করা হয়েছে!")
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
            "❌ ফরম্যাট ভুল হয়েছে।\n"
            "সঠিক ফরম্যাট: <code>/addalias &lt;টার্ম&gt; = &lt;অফিসিয়াল নাম&gt;</code>\n"
            "উদাহরণ: <code>/addalias tony stark stare = Robert Downey Jr Eye Roll</code>",
            parse_mode="HTML",
        )
        return

    term, canonical = payload.split("=", 1)
    term = term.strip().lower()
    canonical = canonical.strip()

    if not term or not canonical:
        await message.answer("❌ টার্ম এবং অফিসিয়াল নাম উভয়ই প্রয়োজন।")
        return

    await add_alias(term, canonical)
    await message.answer(
        f"✅ <b>অ্যালিয়াস সফলভাবে যুক্ত হয়েছে!</b>\n\n"
        f"• <b>সার্চ টার্ম:</b> <code>{term}</code>\n"
        f"• <b>অফিসিয়াল নাম:</b> <b>{canonical}</b>",
        parse_mode="HTML",
    )

@router.message(Command("aliases"))
async def handle_list_aliases_command(message: types.Message):
    """Display registered alias mappings."""
    if not is_admin(message.from_user.id):
        return

    aliases = await get_all_aliases()
    if not aliases:
        await message.answer("⚠️ বর্তমানে কোনো অ্যালিয়াস ম্যাপিং সংরক্ষিত নেই।", parse_mode="HTML")
        return

    lines = ["🔀 <b>সংরক্ষিত অ্যালিয়াস ম্যাপিং:</b>\n"]
    for a in aliases:
        lines.append(f"• <code>{a['alias_term']}</code> ➔ <b>{a['canonical_name']}</b>")

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

    total_templates = len(templates)
    total_banners = len(banners)
    total_aliases = len(aliases)
    total_uses = sum(t.get("usage_count", 0) for t in templates)

    stats_text = (
        f"📊 <b>KBKH Meme Bot স্ট্যাটিসটিক্স:</b>\n\n"
        f"• 🎭 <b>মোট টেমপ্লেট:</b> {total_templates}\n"
        f"• 🚀 <b>সর্বমোট মিম তৈরি:</b> {total_uses} বার\n"
        f"• 📢 <b>সংরক্ষিত ব্যানার:</b> {total_banners}\n"
        f"• 🔀 <b>অ্যালিয়াস ম্যাপিং:</b> {total_aliases} টি"
    )
    await message.answer(stats_text, parse_mode="HTML")
