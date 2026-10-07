from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.queries import add_template
from handlers.channel import extract_template_metadata

router = Router(name="submission_router")

class TemplateUploadSG(StatesGroup):
    waiting_for_media = State()
    waiting_for_title = State()

@router.message(Command("add_template"))
@router.callback_query(F.data == "action_add_template")
async def start_template_submission(event: types.Message | types.CallbackQuery, state: FSMContext):
    """Initiate user template submission flow."""
    message = event if isinstance(event, types.Message) else event.message
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    await state.clear()
    await state.set_state(TemplateUploadSG.waiting_for_media)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="[Cancel]", callback_data="cancel_submission")]]
    )

    await message.answer(
        "[Send the image, video, or document. Send /cancel to abort.]",
        reply_markup=cancel_kb,
    )

@router.callback_query(F.data == "cancel_submission")
@router.message(Command("cancel"))
async def cancel_submission(event: types.Message | types.CallbackQuery, state: FSMContext):
    """Abort template submission flow."""
    current_state = await state.get_state()
    if current_state is None:
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        return

    await state.clear()
    message = event if isinstance(event, types.Message) else event.message
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    await message.answer("[Submission cancelled. Returning to main menu.]")

@router.message(TemplateUploadSG.waiting_for_media, F.photo | F.document | F.video | F.animation)
async def process_submission_media(message: types.Message, state: FSMContext):
    """Validate and store submitted media file."""
    meta = extract_template_metadata(message)
    if not meta:
        await message.answer("[Error: Could not extract valid media. Please send a photo, video, or image document.]")
        return

    file_id, file_unique_id, media_type, auto_title, auto_tags = meta

    await state.update_data(
        file_id=file_id,
        file_unique_id=file_unique_id,
        media_type=media_type,
        auto_title=auto_title,
        auto_tags=auto_tags,
    )
    await state.set_state(TemplateUploadSG.waiting_for_title)

    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="[Skip / Use Default Name]", callback_data="submission_skip_title")],
            [InlineKeyboardButton(text="[Cancel]", callback_data="cancel_submission")],
        ]
    )

    await message.answer(
        f"[Send a title or tags, or send /skip to use default name: '{auto_title}']",
        reply_markup=skip_kb,
    )

@router.message(TemplateUploadSG.waiting_for_media)
async def process_invalid_media(message: types.Message):
    """Handle non-media input in media upload state."""
    await message.answer("[Invalid input. Send an image, video, or document file, or /cancel to abort.]")

@router.callback_query(TemplateUploadSG.waiting_for_title, F.data == "submission_skip_title")
@router.message(TemplateUploadSG.waiting_for_title, Command("skip"))
async def process_skip_title(event: types.Message | types.CallbackQuery, state: FSMContext):
    """Skip custom title and save with default naming."""
    message = event if isinstance(event, types.Message) else event.message
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    data = await state.get_data()
    title = data.get("auto_title") or f"User_Template_{event.from_user.id}"
    tags = data.get("auto_tags") or "user_submission"

    await _finalize_template_save(message, state, data, title, tags, event.from_user.id)

@router.message(TemplateUploadSG.waiting_for_title, F.text, ~F.text.startswith("/"))
async def process_custom_title(message: types.Message, state: FSMContext):
    """Save template with user-provided title and tags."""
    data = await state.get_data()
    raw_text = message.text.strip()

    # Parse title and hashtags
    import re
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    first_line = lines[0] if lines else raw_text
    hashtags = re.findall(r"#(\w+)", raw_text)

    clean_title = re.sub(r"#\w+", "", first_line).strip() or data.get("auto_title") or "User_Template"
    tags_str = ", ".join(hashtags) if hashtags else "user_submission"

    await _finalize_template_save(message, state, data, clean_title, tags_str, message.from_user.id)

async def _finalize_template_save(message: types.Message, state: FSMContext, data: dict, title: str, tags: str, user_id: int):
    """Persist template to database and present action buttons."""
    file_id = data["file_id"]
    file_unique_id = data.get("file_unique_id")
    media_type = data.get("media_type", "photo")

    template_id = await add_template(
        file_id=file_id,
        file_unique_id=file_unique_id,
        media_type=media_type,
        title=title,
        name=title,
        tags=tags,
        added_by=user_id,
        source_channel_id="user_submission",
        source_channel_title=f"User_{user_id}",
    )

    await state.clear()

    success_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="[Use Template]", callback_data=f"btn_create:{template_id}"),
                InlineKeyboardButton(text="[Back to Catalog]", callback_data="menu_browse"),
            ]
        ]
    )

    await message.answer(
        f"[Success: Template added to the catalog.]\n\n"
        f"Title: {title}\n"
        f"ID: #{template_id}\n"
        f"Media: {media_type}\n"
        f"Tags: {tags}",
        reply_markup=success_kb,
    )
