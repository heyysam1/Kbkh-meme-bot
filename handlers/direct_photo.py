"""Direct photo to meme canvas.

Lets a regular user send a photo in private chat and use it straight as the
meme canvas, without the photo entering the public template catalog.

Registered BEFORE admin.router: admin users keep their auto-ingest behavior
via SkipHandler; everyone else gets a confirm step that hands off into the
editor FSM (handlers.editor).
"""
from aiogram import Router, types, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.queries import get_user_lang, upsert_user
from handlers.admin import is_admin
from handlers.editor import EditorSG
from services.i18n import t

router = Router(name="direct_photo_router")


class DirectPhotoSG(StatesGroup):
    waiting_for_confirm = State()


@router.message(StateFilter(None), F.chat.type == "private", F.photo)
async def handle_direct_photo(message: types.Message, state: FSMContext):
    """Offer a privately sent photo as an instant meme canvas (non-admins only)."""
    if is_admin(message.from_user.id):
        # Admins keep the auto-ingest flow; let the next matching handler run.
        raise SkipHandler

    lang = await get_user_lang(message.from_user.id)
    file_id = message.photo[-1].file_id
    await state.clear()
    await state.update_data(direct_file_id=file_id)
    await state.set_state(DirectPhotoSG.waiting_for_confirm)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("misc.create_meme", lang), callback_data="direct_create"),
                InlineKeyboardButton(text=t("misc.cancel", lang), callback_data="direct_cancel"),
            ]
        ]
    )
    await message.answer_photo(
        photo=file_id,
        caption=t("direct.confirm", lang),
        reply_markup=kb,
    )


@router.callback_query(F.data == "direct_create")
async def cb_direct_create(callback: types.CallbackQuery, state: FSMContext):
    """Seed the editor FSM with the confirmed photo and prompt for meme text."""
    lang = await get_user_lang(callback.from_user.id)
    data = await state.get_data()
    direct_file_id = data.get("direct_file_id")
    if not direct_file_id:
        await callback.answer(t("direct.session_expired", lang), show_alert=True)
        await state.clear()
        return

    user = await upsert_user(callback.from_user.id)
    preferred_font = user.get("preferred_font")
    if preferred_font == "default" or not preferred_font:
        preferred_font = None

    # Same seeding as editor.handle_initiate_meme_creation, but template_id=None
    # (direct photos are not catalog templates, so usage is never logged).
    await state.clear()
    await state.update_data(
        template_id=None,
        file_id=direct_file_id,
        title="Direct Photo",
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
        banner_id=None,
        is_clean=False,
    )
    await state.set_state(EditorSG.waiting_for_text)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("misc.cancel", lang), callback_data="edit:cancel")]]
    )
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        t("start.editor_prompt", lang).format(title=t("direct.photo_title", lang)),
        reply_markup=cancel_kb,
    )


@router.callback_query(F.data == "direct_cancel")
async def cb_direct_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Abort the direct-photo flow."""
    await state.clear()
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer(t("misc.cancelled", lang))
    try:
        await callback.message.delete()
    except Exception:
        pass
