import logging
import re
from pathlib import Path
from typing import Optional, Tuple
from aiogram import Router, types, F
from database.queries import add_template

logger = logging.getLogger("kbkh_meme_bot.channel")
router = Router(name="channel_ingestion_router")

def extract_template_metadata(message: types.Message) -> Optional[Tuple[str, Optional[str], str, str, str]]:
    """
    Extract (file_id, file_unique_id, media_type, title, tags) from a message.
    Supports photos, documents, videos, and animations with robust fallback naming.
    """
    file_id = None
    file_unique_id = None
    media_type = "photo"
    suggested_filename = None

    if message.photo:
        photo = message.photo[-1]
        file_id = photo.file_id
        file_unique_id = photo.file_unique_id
        media_type = "photo"
    elif message.animation:
        file_id = message.animation.file_id
        file_unique_id = message.animation.file_unique_id
        media_type = "animation"
        suggested_filename = message.animation.file_name
    elif message.video:
        file_id = message.video.file_id
        file_unique_id = message.video.file_unique_id
        media_type = "video"
        suggested_filename = message.video.file_name
    elif message.document:
        file_id = message.document.file_id
        file_unique_id = message.document.file_unique_id
        media_type = "document"
        suggested_filename = message.document.file_name

    if not file_id:
        return None

    # Parse Caption / Title / Tags
    caption = (message.caption or "").strip()
    if caption:
        lines = [line.strip() for line in caption.splitlines() if line.strip()]
        first_line = lines[0] if lines else ""
        remaining_text = " ".join(lines[1:]) if len(lines) > 1 else ""

        # Extract hashtags from caption
        hashtags = re.findall(r"#(\w+)", caption)
        clean_first_line = re.sub(r"#\w+", "", first_line).strip()
        clean_remaining = re.sub(r"#\w+", "", remaining_text).strip()

        title = clean_first_line if clean_first_line else f"Template {file_id[-6:]}"
        combined_tags = list(hashtags)
        if clean_remaining:
            combined_tags.append(clean_remaining)
        tags_str = ", ".join(combined_tags)
    else:
        # Fallback 1: sanitize filename if available (e.g. funny_cat.mp4 -> funny cat)
        if suggested_filename:
            stem = Path(suggested_filename).stem
            title = re.sub(r"[_\-\.]+", " ", stem).strip()
            tags_str = media_type
        else:
            # Fallback 2: auto-generate Template_{chat_id}_{message_id}
            chat_id_clean = abs(message.chat.id) if message.chat else "0"
            title = f"Template_{chat_id_clean}_{message.message_id}"
            tags_str = media_type

    return file_id, file_unique_id, media_type, title, tags_str

@router.channel_post(F.photo | F.document | F.video | F.animation)
async def handle_channel_media_post(message: types.Message):
    """
    Unrestricted Multi-Channel Ingestion:
    Accepts channel posts from any channel where the bot is present as an admin/member.
    Extracts multi-mime media and records template metadata into the database.
    """
    meta = extract_template_metadata(message)
    if not meta:
        return

    file_id, file_unique_id, media_type, title, tags_str = meta
    channel_id = str(message.chat.id)
    channel_title = message.chat.title or "Channel"

    template_id = await add_template(
        file_id=file_id,
        file_unique_id=file_unique_id,
        media_type=media_type,
        title=title,
        name=title,
        tags=tags_str,
        source_channel_id=channel_id,
        source_channel_title=channel_title,
        is_trending=1 if "trending" in tags_str.lower() else 0,
    )

    logger.info("Ingested %s template from channel '%s' (ID: %s)", media_type, channel_title, file_id)
