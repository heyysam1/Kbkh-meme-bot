import io
import logging
import random
from typing import Optional
from aiogram import Router, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)

from database.queries import (
    get_template_by_id,
    increment_template_usage,
    get_user,
    get_user_lang,
    upsert_user,
    get_all_banners,
    get_banner_by_id,
    save_draft,
    get_draft,
    delete_draft,
    log_template_use,
)
from services.font_manager import font_manager
from services.i18n import t
from services.renderer import render_meme
from handlers.admin import is_admin

logger = logging.getLogger("kbkh_meme_bot.editor")
router = Router(name="editor_router")


class EditorSG(StatesGroup):
    waiting_for_text = State()
    editing = State()
    live_editing = State()  # Backwards compatibility alias


# Quick toggle and testing cycles
LAYOUT_CYCLES = ["overlay", "top_banner", "bottom_banner", "breaking_news"]
COLOR_CYCLES = ["white", "yellow", "cyan", "red", "black"]
STROKE_CYCLES = [0, 2, 5, 8]
CASE_CYCLES = ["raw", "upper", "title"]
FILTER_CYCLES = ["none", "deepfry", "grayscale", "invert"]
WM_POS_CYCLES = ["bottom_right", "bottom_left", "top_left", "top_right", "bottom_center"]
WM_SCALE_CYCLES = [0.5, 1.0, 1.5, 2.0]
ALIGN_CYCLES = ["center", "left", "right"]
STROKE_COLOR_CYCLES = [None, "black", "white", "red", "yellow"]
CROP_CYCLES = ["off", "square", "4:5"]

# One-tap style presets: each sets layout + font + color + stroke + filter at once.
STYLE_PRESETS = {
    "classic": {
        "variant": "overlay", "font_key": "Impact.ttf", "text_color": "white",
        "stroke_width": 4, "filter": "none", "case_mode": "upper",
    },
    "modern": {
        "variant": "top_banner", "font_key": "Poppins-Bold.ttf", "text_color": "black",
        "stroke_width": 0, "filter": "none", "case_mode": "title",
    },
    "bold": {
        "variant": "overlay", "font_key": "Anton-Regular.ttf", "text_color": "yellow",
        "stroke_width": 8, "filter": "none", "case_mode": "upper",
    },
}

# Canonical lookup dictionaries for matrix callbacks
LAYOUT_MAP = {
    "overlay": "overlay",
    "top_banner": "top_banner",
    "bottom_banner": "bottom_banner",
    "breaking": "breaking_news",
    "breaking_news": "breaking_news",
}

FONT_MAP = {
    "anek_bangla": "AnekBangla.ttf",
    "anek_extrabold": "AnekBangla-ExtraBold.ttf",
    "anek_bangla_extrabold": "AnekBangla-ExtraBold.ttf",
    "li_siliguri": "LiSiliguri.ttf",
    "siliguri": "LiSiliguri.ttf",
    "headline_bangla": "HeadlineBangla.ttf",
    "headline": "HeadlineBangla.ttf",
    "noto_sans_bengali": "NotoSansBengali.ttf",
    "noto_bengali": "NotoSansBengali.ttf",
    "impact": "Impact.ttf",
    "anton": "Anton-Regular.ttf",
    "inter": "Inter-Bold.ttf",
    "poppins": "Poppins-Bold.ttf",
    "poppins_bold": "Poppins-Bold.ttf",
}

COLOR_MAP = {
    "white": "white",
    "black": "black",
    "yellow": "yellow",
    "red": "red",
    "cyan": "cyan",
    "#ffffff": "white",
    "#000000": "black",
    "#ffe600": "yellow",
    "#ff2222": "red",
    "#00e5ff": "cyan",
}

WM_POS_MAP = {
    "tl": "top_left",
    "tr": "top_right",
    "bl": "bottom_left",
    "br": "bottom_right",
    "bc": "bottom_center",
    "top_left": "top_left",
    "top_right": "top_right",
    "bottom_left": "bottom_left",
    "bottom_right": "bottom_right",
    "bottom_center": "bottom_center",
}

WM_POS_DISPLAY = {
    "top_left": "editor.pos_tl",
    "top_right": "editor.pos_tr",
    "bottom_left": "editor.pos_bl",
    "bottom_right": "editor.pos_br",
    "bottom_center": "editor.pos_bc",
    "tl": "editor.pos_tl",
    "tr": "editor.pos_tr",
    "bl": "editor.pos_bl",
    "br": "editor.pos_br",
    "bc": "editor.pos_bc",
}


async def _lang_of(user_id: int) -> str:
    """Fetch the user's UI language, defaulting to Bangla on any failure."""
    try:
        return await get_user_lang(user_id)
    except Exception:
        return "bn"


# ------------------------------------------------------------------------------
# Pure helpers (unit-testable, no Telegram I/O)
# ------------------------------------------------------------------------------

_UNDO_EXCLUDE = {"history", "preview_message_id", "template_bytes"}


def make_snapshot(data: dict) -> dict:
    """Copy editor state minus undo history and volatile keys."""
    return {k: v for k, v in data.items() if k not in _UNDO_EXCLUDE}


def push_snapshot(history: Optional[list], snap: dict, limit: int = 10) -> list:
    """Append a snapshot, keeping at most `limit` entries."""
    return (list(history or []) + [snap])[-limit:]


def pop_snapshot(history: Optional[list]):
    """Return (previous_state, remaining_history); (None, []) when empty."""
    hist = list(history or [])
    if not hist:
        return None, []
    return hist[-1], hist[:-1]


async def _push_history(state: FSMContext, data: dict) -> None:
    """Snapshot current editor state before a mutating action (for Undo)."""
    await state.update_data(history=push_snapshot(data.get("history"), make_snapshot(data)))


def apply_preset(data: dict, name: str) -> dict:
    """Return editor state with the named style preset applied (pure)."""
    preset = STYLE_PRESETS.get(name)
    if not preset:
        return dict(data)
    new = dict(data)
    new.update(preset)
    return new


def shuffled_style() -> dict:
    """Roll a random font + color + stroke + filter combo (pure)."""
    fonts = list(dict.fromkeys(FONT_MAP.values()))
    return {
        "font_key": random.choice(fonts),
        "text_color": random.choice(COLOR_CYCLES),
        "stroke_width": random.choice(STROKE_CYCLES),
        "filter": random.choice(FILTER_CYCLES),
    }


def _clamp_float(value, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# ------------------------------------------------------------------------------
# Display label helpers (i18n)
# ------------------------------------------------------------------------------

def _variant_label(variant: str, lang: str) -> str:
    return {
        "overlay": t("editor.layout_overlay", lang),
        "top_banner": t("editor.layout_top_banner", lang),
        "bottom_banner": t("editor.layout_bottom_banner", lang),
        "breaking_news": t("editor.layout_breaking", lang),
    }.get(variant, variant.replace("_", " ").upper())


def _color_label(color: str, lang: str) -> str:
    return {
        "white": t("editor.color_white", lang),
        "#ffffff": t("editor.color_white", lang),
        "black": t("editor.color_black", lang),
        "#000000": t("editor.color_black", lang),
        "yellow": t("editor.color_yellow", lang),
        "#ffe600": t("editor.color_yellow", lang),
        "red": t("editor.color_red", lang),
        "#ff2222": t("editor.color_red", lang),
        "cyan": t("editor.color_cyan", lang),
        "#00e5ff": t("editor.color_cyan", lang),
    }.get(str(color).lower(), str(color).upper())


def _case_label(case: str, lang: str) -> str:
    return {
        "raw": t("editor.case_raw", lang),
        "upper": t("editor.case_upper", lang),
        "title": t("editor.case_title", lang),
    }.get(case, case.upper())


def _fx_label(fx: str, lang: str) -> str:
    return {
        "none": t("editor.fx_none", lang),
        "deepfry": t("editor.fx_deepfry", lang),
        "grayscale": t("editor.fx_grayscale", lang),
        "invert": t("editor.fx_invert", lang),
    }.get(fx, fx.upper())


def _align_label(align: str, lang: str) -> str:
    return {
        "left": t("editor.align_left", lang),
        "center": t("editor.align_center", lang),
        "right": t("editor.align_right", lang),
    }.get(align, align)


def _crop_label(crop: str, lang: str) -> str:
    return {
        "off": t("editor.crop_off", lang),
        "square": t("editor.crop_square", lang),
        "4:5": t("editor.crop_45", lang),
    }.get(crop, crop)


def _on_off(value: bool, lang: str) -> str:
    return t("editor.on", lang) if value else t("editor.off", lang)


# ------------------------------------------------------------------------------
# Keyboard Builders
# ------------------------------------------------------------------------------

def get_editor_keyboard(data: dict, lang: str = "bn") -> InlineKeyboardMarkup:
    """Build full stateful inline control dashboard for meme editor."""
    raw_var = data.get("variant", "overlay")
    variant = _variant_label(raw_var, lang)
    color = _color_label(data.get("text_color", "white"), lang)
    stroke = f"{data.get('stroke_width', 4)}px"
    fx = _fx_label(data.get("filter", "none"), lang)
    wm_state = _on_off(bool(data.get("watermark_enabled", True)), lang)
    raw_pos = data.get("watermark_pos", "bottom_right")
    wm_pos = t(WM_POS_DISPLAY.get(raw_pos, "editor.pos_br"), lang)
    wm_scale = f"{data.get('watermark_scale', 1.0)}x"

    font_key = data.get("font_key")
    font_name = "Select"
    if font_key:
        font_name = font_manager.sanitize_display_name(font_key)[:10]

    case = _case_label(data.get("case_mode", "raw"), lang)
    logo_state = _on_off(not data.get("is_clean", False), lang)

    align = _align_label(data.get("text_align", "center"), lang)
    sc = data.get("stroke_color")
    stroke_color = t("editor.scolor_auto", lang) if not sc else _color_label(sc, lang)
    text_bg = _on_off(bool(data.get("text_bg", False)), lang)
    flip = _on_off(bool(data.get("flip", False)), lang)
    crop = _crop_label(data.get("crop", "off"), lang)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("editor.btn_layout", lang).format(v=variant), callback_data="edit:menu:layout"),
                InlineKeyboardButton(text=t("editor.btn_color", lang).format(v=color), callback_data="edit:menu:color"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_stroke", lang).format(v=stroke), callback_data="edit:menu:stroke"),
                InlineKeyboardButton(text=t("editor.btn_filter", lang).format(v=fx), callback_data="edit:menu:fx"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_case", lang).format(v=case), callback_data="ed_cycle_case"),
                InlineKeyboardButton(text=t("editor.btn_logo", lang).format(v=logo_state), callback_data="ed_toggle_clean"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_font", lang).format(v=font_name), callback_data="edit:menu:font"),
                InlineKeyboardButton(text=t("editor.btn_watermark", lang).format(v=wm_state), callback_data="edit:wm_toggle"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_wm_pos", lang).format(v=wm_pos), callback_data="edit:menu:wm_pos"),
                InlineKeyboardButton(text=t("editor.btn_wm_scale", lang).format(v=wm_scale), callback_data="edit:menu:wm_scale"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_banner", lang), callback_data="edit:banner:menu"),
            ],
            # ---- Fine-tune section ----
            [
                InlineKeyboardButton(text=t("editor.btn_nudge_up", lang), callback_data="edit:nudge:up"),
                InlineKeyboardButton(text=t("editor.btn_nudge_down", lang), callback_data="edit:nudge:down"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_fsize_down", lang), callback_data="edit:fsize:down"),
                InlineKeyboardButton(text=t("editor.btn_fsize_up", lang), callback_data="edit:fsize:up"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_align", lang).format(v=align), callback_data="edit:align:cycle"),
                InlineKeyboardButton(text=t("editor.btn_stroke_color", lang).format(v=stroke_color), callback_data="edit:strokecolor:cycle"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_text_bg", lang).format(v=text_bg), callback_data="edit:textbg:toggle"),
                InlineKeyboardButton(text=t("editor.btn_flip", lang).format(v=flip), callback_data="edit:flip:toggle"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_crop", lang).format(v=crop), callback_data="edit:crop:cycle"),
                InlineKeyboardButton(text=t("editor.btn_undo", lang), callback_data="edit:undo"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_bright_down", lang), callback_data="edit:bright:down"),
                InlineKeyboardButton(text=t("editor.btn_bright_up", lang), callback_data="edit:bright:up"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_contrast_down", lang), callback_data="edit:contrast:down"),
                InlineKeyboardButton(text=t("editor.btn_contrast_up", lang), callback_data="edit:contrast:up"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_presets", lang), callback_data="edit:menu:presets"),
                InlineKeyboardButton(text=t("editor.btn_shuffle", lang), callback_data="edit:shuffle"),
            ],
            # ---- end fine-tune section ----
            [
                InlineKeyboardButton(text=t("editor.btn_save_draft", lang), callback_data="edit:save_draft"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_export_photo", lang), callback_data="edit:export:photo"),
                InlineKeyboardButton(text=t("editor.btn_export_doc", lang), callback_data="edit:export:doc"),
            ],
            [
                InlineKeyboardButton(text=t("editor.btn_edit_text", lang), callback_data="edit:change_text"),
                InlineKeyboardButton(text=t("editor.btn_reset", lang), callback_data="edit:reset"),
                InlineKeyboardButton(text=t("editor.btn_cancel", lang), callback_data="edit:cancel"),
            ],
        ]
    )


def get_layout_matrix_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build Layout Selector Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("editor.layout_overlay", lang), callback_data="edit:layout:overlay"),
                InlineKeyboardButton(text=t("editor.layout_top_banner", lang), callback_data="edit:layout:top_banner"),
            ],
            [
                InlineKeyboardButton(text=t("editor.layout_bottom_banner", lang), callback_data="edit:layout:bottom_banner"),
                InlineKeyboardButton(text=t("editor.layout_breaking", lang), callback_data="edit:layout:breaking"),
            ],
            [
                InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back"),
            ],
        ]
    )


def get_font_matrix_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build Font Selector Matrix for all 9 approved fonts."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Anek Bangla", callback_data="edit:font:anek_bangla"),
                InlineKeyboardButton(text="Anek ExtraBold", callback_data="edit:font:anek_extrabold"),
            ],
            [
                InlineKeyboardButton(text="Li Siliguri", callback_data="edit:font:li_siliguri"),
                InlineKeyboardButton(text="Headline Bangla", callback_data="edit:font:headline_bangla"),
            ],
            [
                InlineKeyboardButton(text="Noto Sans Bengali", callback_data="edit:font:noto_sans_bengali"),
                InlineKeyboardButton(text="Impact", callback_data="edit:font:impact"),
            ],
            [
                InlineKeyboardButton(text="Anton", callback_data="edit:font:anton"),
                InlineKeyboardButton(text="Inter", callback_data="edit:font:inter"),
            ],
            [
                InlineKeyboardButton(text="Poppins Bold", callback_data="edit:font:poppins"),
            ],
            [
                InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back"),
            ],
        ]
    )


def get_color_matrix_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build Color & Palette Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("editor.color_white", lang), callback_data="edit:color:#FFFFFF"),
                InlineKeyboardButton(text=t("editor.color_black", lang), callback_data="edit:color:#000000"),
            ],
            [
                InlineKeyboardButton(text=t("editor.color_yellow", lang), callback_data="edit:color:#FFE600"),
                InlineKeyboardButton(text=t("editor.color_red", lang), callback_data="edit:color:#FF2222"),
            ],
            [
                InlineKeyboardButton(text=t("editor.color_cyan", lang), callback_data="edit:color:#00E5FF"),
            ],
            [
                InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back"),
            ],
        ]
    )


def get_stroke_matrix_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build Stroke Width Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="0px", callback_data="edit:stroke:0"),
                InlineKeyboardButton(text="2px", callback_data="edit:stroke:2"),
            ],
            [
                InlineKeyboardButton(text="5px", callback_data="edit:stroke:5"),
                InlineKeyboardButton(text="8px", callback_data="edit:stroke:8"),
            ],
            [
                InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back"),
            ],
        ]
    )


def get_wm_pos_matrix_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build Watermark Position Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("editor.pos_tl", lang), callback_data="edit:wm_pos:tl"),
                InlineKeyboardButton(text=t("editor.pos_tr", lang), callback_data="edit:wm_pos:tr"),
            ],
            [
                InlineKeyboardButton(text=t("editor.pos_bl", lang), callback_data="edit:wm_pos:bl"),
                InlineKeyboardButton(text=t("editor.pos_br", lang), callback_data="edit:wm_pos:br"),
            ],
            [
                InlineKeyboardButton(text=t("editor.pos_bc", lang), callback_data="edit:wm_pos:bc"),
            ],
            [
                InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back"),
            ],
        ]
    )


def get_wm_scale_matrix_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build Watermark Scale Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="0.5x", callback_data="edit:wm_scale:0.5"),
                InlineKeyboardButton(text="1.0x", callback_data="edit:wm_scale:1.0"),
            ],
            [
                InlineKeyboardButton(text="1.5x", callback_data="edit:wm_scale:1.5"),
                InlineKeyboardButton(text="2.0x", callback_data="edit:wm_scale:2.0"),
            ],
            [
                InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back"),
            ],
        ]
    )


def get_fx_matrix_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build Canvas FX Filters Matrix."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("editor.fx_none", lang), callback_data="edit:fx:none"),
                InlineKeyboardButton(text=t("editor.fx_deepfry", lang), callback_data="edit:fx:deepfry"),
            ],
            [
                InlineKeyboardButton(text=t("editor.fx_grayscale", lang), callback_data="edit:fx:grayscale"),
                InlineKeyboardButton(text=t("editor.fx_invert", lang), callback_data="edit:fx:invert"),
            ],
            [
                InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back"),
            ],
        ]
    )


def get_preset_keyboard(lang: str = "bn") -> InlineKeyboardMarkup:
    """Build one-tap style preset menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("editor.preset_classic", lang), callback_data="edit:preset:classic"),
                InlineKeyboardButton(text=t("editor.preset_modern", lang), callback_data="edit:preset:modern"),
            ],
            [
                InlineKeyboardButton(text=t("editor.preset_bold", lang), callback_data="edit:preset:bold"),
            ],
            [
                InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back"),
            ],
        ]
    )


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

async def _fetch_telegram_file_bytes(bot, file_id: str) -> Optional[bytes]:
    """Download Telegram file into memory buffer."""
    try:
        buffer = io.BytesIO()
        await bot.download(file=file_id, destination=buffer)
        return buffer.getvalue()
    except Exception:
        return None


async def _render_current_draft(bot, data: dict, user_id: int) -> Optional[io.BytesIO]:
    """Execute rendering pipeline using current FSM state parameters."""
    raw_template_bytes = data.get("template_bytes")
    if not raw_template_bytes:
        file_id = data.get("file_id")
        if file_id:
            raw_template_bytes = await _fetch_telegram_file_bytes(bot, file_id)
            data["template_bytes"] = raw_template_bytes

    if not raw_template_bytes:
        return None

    # Resolve font path
    font_key = data.get("font_key")
    text = data.get("text", "")
    script = font_manager.detect_script(text)
    font_path = font_manager.get_font_path(font_key, script=script)

    # Fetch user watermark bytes if enabled
    watermark_bytes = None
    watermark_text = None
    user = await get_user(user_id)
    if user and user.get("watermark_file_id"):
        watermark_bytes = await _fetch_telegram_file_bytes(bot, user["watermark_file_id"])
    elif user and user.get("watermark_text"):
        watermark_text = user["watermark_text"]

    # Fetch attached promotional banner bytes, if any
    banner_bytes = None
    banner_id = data.get("banner_id")
    if banner_id:
        try:
            banner = await get_banner_by_id(int(banner_id))
        except (TypeError, ValueError):
            banner = None
        if banner and banner.get("file_id"):
            banner_bytes = await _fetch_telegram_file_bytes(bot, banner["file_id"])

    return await render_meme(
        template_bytes=raw_template_bytes,
        text=text,
        font_path=font_path,
        variant=data.get("variant", "overlay"),
        is_clean=data.get("is_clean", False),
        text_color=data.get("text_color", "white"),
        stroke_width=data.get("stroke_width", 4),
        filter_name=data.get("filter", "none"),
        case_mode=data.get("case_mode", "raw"),
        watermark_bytes=watermark_bytes,
        watermark_text=watermark_text,
        watermark_pos=data.get("watermark_pos", "bottom_right"),
        watermark_scale=data.get("watermark_scale", 1.0),
        watermark_opacity=data.get("watermark_opacity", 0.8),
        watermark_enabled=data.get("watermark_enabled", True),
        banner_bytes=banner_bytes,
        text_offset_y=int(data.get("text_offset_y", 0) or 0),
        font_scale=_clamp_float(data.get("font_scale", 1.0), 0.5, 2.0, 1.0),
        text_align=data.get("text_align", "center") or "center",
        stroke_color=data.get("stroke_color"),
        text_bg=bool(data.get("text_bg", False)),
        flip=bool(data.get("flip", False)),
        crop=data.get("crop", "off") or "off",
        brightness=_clamp_float(data.get("brightness", 1.0), 0.5, 2.0, 1.0),
        contrast=_clamp_float(data.get("contrast", 1.0), 0.5, 2.0, 1.0),
    )


async def _update_live_preview(callback: types.CallbackQuery, state: FSMContext, data: dict,
                              lang: str = "bn", custom_kb: Optional[InlineKeyboardMarkup] = None):
    """Re-render in-memory and update Telegram preview image without deleting the message."""
    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        try:
            await callback.answer(t("editor.err_preview", lang), show_alert=True)
        except Exception:
            pass
        return

    input_media = InputMediaPhoto(
        media=BufferedInputFile(rendered_buf.getvalue(), filename="meme_preview.jpg"),
        caption=t("editor.live_preview", lang),
    )
    kb = custom_kb or get_editor_keyboard(data, lang)

    try:
        await callback.message.edit_media(media=input_media, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("edit_media error: %s", e)
    except Exception as e:
        logger.warning("Failed to update preview: %s", e)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


async def _show_submenu(callback: types.CallbackQuery, kb: InlineKeyboardMarkup, title: str = ""):
    """Switch keyboard to selector matrix menu without redownloading image."""
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("edit_reply_markup error: %s", e)
    except Exception as e:
        logger.warning("Failed to update menu: %s", e)
    finally:
        try:
            await callback.answer(title)
        except Exception:
            pass


# ------------------------------------------------------------------------------
# Entry into Meme Flow & Text Submission
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("btn_raw:"))
async def handle_download_raw_template(callback: types.CallbackQuery):
    """Directly dispatch raw template files without text entry."""
    lang = await _lang_of(callback.from_user.id)
    template_id_str = callback.data.split(":", 1)[1]
    if not template_id_str.isdigit():
        await callback.answer(t("editor.err_invalid_template_id", lang), show_alert=True)
        return

    template = await get_template_by_id(int(template_id_str))
    if not template:
        await callback.answer(t("editor.err_template_not_found", lang), show_alert=True)
        return

    file_id = template["file_id"]
    title = template.get("title") or template.get("name") or "Template"
    media_type = template.get("media_type") or "photo"

    await callback.answer(t("editor.sending_raw", lang))

    bot = callback.message.bot
    chat_id = callback.message.chat.id
    if media_type == "video":
        await bot.send_video(
            chat_id=chat_id,
            video=file_id,
            caption=t("editor.raw_video", lang).format(title=title),
        )
    elif media_type == "animation":
        await bot.send_animation(
            chat_id=chat_id,
            animation=file_id,
            caption=t("editor.raw_animation", lang).format(title=title),
        )
    elif media_type == "document":
        await bot.send_document(
            chat_id=chat_id,
            document=file_id,
            caption=t("editor.raw_document", lang).format(title=title),
        )
    else:
        await bot.send_photo(
            chat_id=chat_id,
            photo=file_id,
            caption=t("editor.raw_photo", lang).format(title=title),
        )
        await bot.send_document(
            chat_id=chat_id,
            document=file_id,
            caption=t("editor.raw_document", lang).format(title=title),
        )


@router.callback_query(F.data.startswith("btn_create:") | F.data.startswith("cb_create_meme:"))
async def handle_initiate_meme_creation(callback: types.CallbackQuery, state: FSMContext):
    """Triggered when user clicks Create Meme on any catalog item."""
    lang = await _lang_of(callback.from_user.id)
    template_id_str = callback.data.split(":", 1)[1]
    if not template_id_str.isdigit():
        await callback.answer(t("editor.err_invalid_template_id", lang), show_alert=True)
        return

    template = await get_template_by_id(int(template_id_str))
    if not template:
        await callback.answer(t("editor.err_template_not_found", lang), show_alert=True)
        return

    await callback.answer()
    user = await upsert_user(callback.from_user.id)
    try:
        await log_template_use(callback.from_user.id, template["id"])
    except Exception:
        pass
    preferred_font = user.get("preferred_font")
    if preferred_font == "default" or not preferred_font:
        preferred_font = None

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
        text_offset_y=0,
        font_scale=1.0,
        text_align="center",
        stroke_color=None,
        text_bg=False,
        flip=False,
        crop="off",
        brightness=1.0,
        contrast=1.0,
        banner_id=None,
    )
    await state.set_state(EditorSG.waiting_for_text)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("editor.btn_cancel", lang), callback_data="edit:cancel")]]
    )

    title = template.get("title") or template.get("name") or "Template"
    await callback.message.answer(
        t("editor.enter_text", lang).format(title=title),
        reply_markup=cancel_kb,
    )


# ------------------------------------------------------------------------------
# Meme Caption Text Input (Strict Non-Slash Filtering)
# ------------------------------------------------------------------------------

@router.message(EditorSG.waiting_for_text, F.text, ~F.text.startswith("/"))
async def handle_meme_text_input(message: types.Message, state: FSMContext):
    """Receive caption, generate initial preview, and open full inline editing keyboard."""
    lang = await _lang_of(message.from_user.id)
    raw_text = message.text.strip()
    MAX_CAPTION_LEN = 300
    if len(raw_text) > MAX_CAPTION_LEN:
        await message.answer(t("editor.err_caption_too_long", lang).format(max=MAX_CAPTION_LEN, n=len(raw_text)))
        return

    data = await state.get_data()
    await _push_history(state, data)
    data["text"] = raw_text
    await state.update_data(text=raw_text)
    await state.set_state(EditorSG.editing)

    status_msg = await message.answer(t("editor.rendering_preview", lang))

    rendered_buf = await _render_current_draft(message.bot, data, message.from_user.id)
    try:
        await status_msg.delete()
    except Exception:
        pass

    if not rendered_buf:
        await message.answer(t("editor.err_render_failed", lang))
        return

    input_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_preview.jpg")
    kb = get_editor_keyboard(data, lang)

    preview_msg = await message.answer_photo(
        photo=input_file,
        caption=t("editor.live_preview", lang),
        reply_markup=kb,
    )
    await state.update_data(preview_message_id=preview_msg.message_id)


# Backward compatibility alias
handle_receive_editor_text = handle_meme_text_input


@router.message(EditorSG.editing, F.text, ~F.text.startswith("/"))
async def handle_stray_text_in_editor(message: types.Message):
    """Guide users who type text mid-edit instead of silently ignoring it."""
    lang = await _lang_of(message.from_user.id)
    await message.answer(t("editor.hint_stray_text", lang))


@router.message(EditorSG.editing, F.photo)
async def handle_stray_photo_in_editor(message: types.Message):
    """Guide users who send a photo mid-edit instead of silently ignoring it."""
    lang = await _lang_of(message.from_user.id)
    await message.answer(t("editor.hint_stray_photo", lang))


# ------------------------------------------------------------------------------
# Submenu Openers
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "edit:menu:layout")
async def cb_open_layout_menu(callback: types.CallbackQuery):
    """Open Layout Matrix menu."""
    lang = await _lang_of(callback.from_user.id)
    await _show_submenu(callback, get_layout_matrix_keyboard(lang), t("editor.menu_layout", lang))


@router.callback_query(F.data == "edit:menu:font" | F.data == "ed_menu_font")
async def cb_open_font_menu(callback: types.CallbackQuery):
    """Open Font Selector Matrix menu."""
    lang = await _lang_of(callback.from_user.id)
    await _show_submenu(callback, get_font_matrix_keyboard(lang), t("editor.menu_font", lang))


@router.callback_query(F.data == "edit:menu:color")
async def cb_open_color_menu(callback: types.CallbackQuery):
    """Open Color & Palette Matrix menu."""
    lang = await _lang_of(callback.from_user.id)
    await _show_submenu(callback, get_color_matrix_keyboard(lang), t("editor.menu_color", lang))


@router.callback_query(F.data == "edit:menu:stroke")
async def cb_open_stroke_menu(callback: types.CallbackQuery):
    """Open Stroke Width Matrix menu."""
    lang = await _lang_of(callback.from_user.id)
    await _show_submenu(callback, get_stroke_matrix_keyboard(lang), t("editor.menu_stroke", lang))


@router.callback_query(F.data == "edit:menu:wm_pos")
async def cb_open_wm_pos_menu(callback: types.CallbackQuery):
    """Open Watermark Position Matrix menu."""
    lang = await _lang_of(callback.from_user.id)
    await _show_submenu(callback, get_wm_pos_matrix_keyboard(lang), t("editor.menu_wm_pos", lang))


@router.callback_query(F.data == "edit:menu:wm_scale")
async def cb_open_wm_scale_menu(callback: types.CallbackQuery):
    """Open Watermark Scale Matrix menu."""
    lang = await _lang_of(callback.from_user.id)
    await _show_submenu(callback, get_wm_scale_matrix_keyboard(lang), t("editor.menu_wm_scale", lang))


@router.callback_query(F.data == "edit:menu:fx")
async def cb_open_fx_menu(callback: types.CallbackQuery):
    """Open Canvas FX Filter Matrix menu."""
    lang = await _lang_of(callback.from_user.id)
    await _show_submenu(callback, get_fx_matrix_keyboard(lang), t("editor.menu_fx", lang))


@router.callback_query(F.data == "edit:menu:presets")
async def cb_open_preset_menu(callback: types.CallbackQuery):
    """Open one-tap style preset menu."""
    lang = await _lang_of(callback.from_user.id)
    await _show_submenu(callback, get_preset_keyboard(lang), t("editor.preset_title", lang))


@router.callback_query(F.data == "edit:back" | F.data == "ed_back_to_editor")
async def cb_back_to_dashboard(callback: types.CallbackQuery, state: FSMContext):
    """Return to main editor controls without re-rendering."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _show_submenu(callback, get_editor_keyboard(data, lang), t("editor.back_to_editor", lang))


# ------------------------------------------------------------------------------
# Layout Matrix Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:layout:"))
async def cb_set_layout(callback: types.CallbackQuery, state: FSMContext):
    """Apply layout variant and immediately update preview."""
    lang = await _lang_of(callback.from_user.id)
    layout_type = callback.data.split(":", 2)[2].strip()
    new_variant = LAYOUT_MAP.get(layout_type, layout_type)

    data = await state.get_data()
    await _push_history(state, data)
    data["variant"] = new_variant
    await state.update_data(variant=new_variant)
    await _update_live_preview(callback, state, data, lang=lang)


# ------------------------------------------------------------------------------
# Font Selector Matrix Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:font:") | F.data.startswith("ed_set_font:"))
async def cb_set_font(callback: types.CallbackQuery, state: FSMContext):
    """Apply typography font from curated catalog and immediately update preview."""
    lang = await _lang_of(callback.from_user.id)
    parts = callback.data.split(":")
    raw_key = parts[2] if len(parts) > 2 else parts[1]
    canonical_font = FONT_MAP.get(raw_key, raw_key)

    data = await state.get_data()
    await _push_history(state, data)
    data["font_key"] = canonical_font
    await state.update_data(font_key=canonical_font)
    await _update_live_preview(callback, state, data, lang=lang)


# ------------------------------------------------------------------------------
# Color & Stroke Matrix Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:color:"))
async def cb_set_color(callback: types.CallbackQuery, state: FSMContext):
    """Apply text color and immediately update preview."""
    lang = await _lang_of(callback.from_user.id)
    color_val = callback.data.split(":", 2)[2].strip()
    data = await state.get_data()
    await _push_history(state, data)
    data["text_color"] = color_val
    await state.update_data(text_color=color_val)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data.startswith("edit:stroke:"))
async def cb_set_stroke(callback: types.CallbackQuery, state: FSMContext):
    """Apply stroke width and immediately update preview."""
    lang = await _lang_of(callback.from_user.id)
    stroke_val_str = callback.data.split(":", 2)[2].strip()
    stroke_val = int(stroke_val_str) if stroke_val_str.isdigit() else 4

    data = await state.get_data()
    await _push_history(state, data)
    data["stroke_width"] = stroke_val
    await state.update_data(stroke_width=stroke_val)
    await _update_live_preview(callback, state, data, lang=lang)


# ------------------------------------------------------------------------------
# Watermark Engine Matrix Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:wm_pos:"))
async def cb_set_wm_pos(callback: types.CallbackQuery, state: FSMContext):
    """Apply watermark anchor position and immediately update preview."""
    lang = await _lang_of(callback.from_user.id)
    pos_code = callback.data.split(":", 2)[2].strip()
    canonical_pos = WM_POS_MAP.get(pos_code, pos_code)

    data = await state.get_data()
    await _push_history(state, data)
    data["watermark_pos"] = canonical_pos
    await state.update_data(watermark_pos=canonical_pos)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data.startswith("edit:wm_scale:"))
async def cb_set_wm_scale(callback: types.CallbackQuery, state: FSMContext):
    """Apply watermark scale multiplier and immediately update preview."""
    lang = await _lang_of(callback.from_user.id)
    scale_str = callback.data.split(":", 2)[2].strip()
    try:
        scale_val = float(scale_str)
    except ValueError:
        scale_val = 1.0

    data = await state.get_data()
    await _push_history(state, data)
    data["watermark_scale"] = scale_val
    await state.update_data(watermark_scale=scale_val)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:wm_toggle" | F.data == "ed_toggle_wm")
async def cb_toggle_wm(callback: types.CallbackQuery, state: FSMContext):
    """Toggle watermark ON/OFF and immediately update preview."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    new_wm = not data.get("watermark_enabled", True)
    if new_wm:
        # Enabling is pointless when the user never configured a watermark;
        # say so instead of silently toggling a no-op flag.
        try:
            user = await get_user(callback.from_user.id)
        except Exception:
            user = None
        if user and not user.get("watermark_file_id") and not user.get("watermark_text"):
            await callback.answer(t("editor.wm_not_set", lang), show_alert=True)
            return
    await _push_history(state, data)
    data["watermark_enabled"] = new_wm
    await state.update_data(watermark_enabled=new_wm)
    await _update_live_preview(callback, state, data, lang=lang)


# ------------------------------------------------------------------------------
# Canvas FX Filters Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:fx:"))
async def cb_set_fx(callback: types.CallbackQuery, state: FSMContext):
    """Apply Canvas FX filter and immediately update preview."""
    lang = await _lang_of(callback.from_user.id)
    filter_val = callback.data.split(":", 2)[2].strip()
    data = await state.get_data()
    await _push_history(state, data)
    data["filter"] = filter_val
    await state.update_data(filter=filter_val)
    await _update_live_preview(callback, state, data, lang=lang)


# ------------------------------------------------------------------------------
# Backward-Compatible Quick Cycle Handlers
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "ed_cycle_layout")
async def cb_cycle_layout(callback: types.CallbackQuery, state: FSMContext):
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("variant", "overlay")
    idx = (LAYOUT_CYCLES.index(curr) + 1) % len(LAYOUT_CYCLES) if curr in LAYOUT_CYCLES else 0
    new_var = LAYOUT_CYCLES[idx]
    data["variant"] = new_var
    await state.update_data(variant=new_var)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "ed_cycle_color")
async def cb_cycle_color(callback: types.CallbackQuery, state: FSMContext):
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("text_color", "white")
    idx = (COLOR_CYCLES.index(curr) + 1) % len(COLOR_CYCLES) if curr in COLOR_CYCLES else 0
    new_color = COLOR_CYCLES[idx]
    data["text_color"] = new_color
    await state.update_data(text_color=new_color)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "ed_cycle_stroke")
async def cb_cycle_stroke(callback: types.CallbackQuery, state: FSMContext):
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("stroke_width", 4)
    idx = (STROKE_CYCLES.index(curr) + 1) % len(STROKE_CYCLES) if curr in STROKE_CYCLES else 0
    new_stroke = STROKE_CYCLES[idx]
    data["stroke_width"] = new_stroke
    await state.update_data(stroke_width=new_stroke)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "ed_cycle_case")
async def cb_cycle_case(callback: types.CallbackQuery, state: FSMContext):
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("case_mode", "raw")
    idx = (CASE_CYCLES.index(curr) + 1) % len(CASE_CYCLES) if curr in CASE_CYCLES else 0
    new_case = CASE_CYCLES[idx]
    data["case_mode"] = new_case
    await state.update_data(case_mode=new_case)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "ed_cycle_filter")
async def cb_cycle_filter(callback: types.CallbackQuery, state: FSMContext):
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("filter", "none")
    idx = (FILTER_CYCLES.index(curr) + 1) % len(FILTER_CYCLES) if curr in FILTER_CYCLES else 0
    new_filter = FILTER_CYCLES[idx]
    data["filter"] = new_filter
    await state.update_data(filter=new_filter)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "ed_cycle_wm_pos")
async def cb_cycle_wm_pos(callback: types.CallbackQuery, state: FSMContext):
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("watermark_pos", "bottom_right")
    idx = (WM_POS_CYCLES.index(curr) + 1) % len(WM_POS_CYCLES) if curr in WM_POS_CYCLES else 0
    new_pos = WM_POS_CYCLES[idx]
    data["watermark_pos"] = new_pos
    await state.update_data(watermark_pos=new_pos)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "ed_toggle_clean")
async def cb_toggle_clean(callback: types.CallbackQuery, state: FSMContext):
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    new_clean = not data.get("is_clean", False)
    data["is_clean"] = new_clean
    await state.update_data(is_clean=new_clean)
    await _update_live_preview(callback, state, data, lang=lang)


# ------------------------------------------------------------------------------
# Fine-Tune Controls: nudge, font size, align, stroke color, text bg, flip,
# crop, brightness, contrast, undo, presets, shuffle
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "edit:nudge:up" | F.data == "edit:nudge:down")
async def cb_nudge_text(callback: types.CallbackQuery, state: FSMContext):
    """Nudge overlay text vertically by 20px."""
    lang = await _lang_of(callback.from_user.id)
    delta = -20 if callback.data.endswith(":up") else 20
    data = await state.get_data()
    await _push_history(state, data)
    try:
        cur = int(data.get("text_offset_y", 0) or 0)
    except (TypeError, ValueError):
        cur = 0
    new_off = max(-200, min(200, cur + delta))
    data["text_offset_y"] = new_off
    await state.update_data(text_offset_y=new_off)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:fsize:up" | F.data == "edit:fsize:down")
async def cb_font_size(callback: types.CallbackQuery, state: FSMContext):
    """Scale the auto-fit font size in 0.1 steps (range 0.5 - 2.0)."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    cur = _clamp_float(data.get("font_scale", 1.0), 0.5, 2.0, 1.0)
    step = 0.1 if callback.data.endswith(":up") else -0.1
    new_scale = round(max(0.5, min(2.0, cur + step)), 1)
    data["font_scale"] = new_scale
    await state.update_data(font_scale=new_scale)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:align:cycle")
async def cb_cycle_align(callback: types.CallbackQuery, state: FSMContext):
    """Cycle text alignment: center -> left -> right."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("text_align", "center")
    idx = (ALIGN_CYCLES.index(curr) + 1) % len(ALIGN_CYCLES) if curr in ALIGN_CYCLES else 0
    new_align = ALIGN_CYCLES[idx]
    data["text_align"] = new_align
    await state.update_data(text_align=new_align)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:strokecolor:cycle")
async def cb_cycle_stroke_color(callback: types.CallbackQuery, state: FSMContext):
    """Cycle stroke color: auto -> black -> white -> red -> yellow."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("stroke_color")
    idx = (STROKE_COLOR_CYCLES.index(curr) + 1) % len(STROKE_COLOR_CYCLES) if curr in STROKE_COLOR_CYCLES else 0
    new_sc = STROKE_COLOR_CYCLES[idx]
    data["stroke_color"] = new_sc
    await state.update_data(stroke_color=new_sc)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:textbg:toggle")
async def cb_toggle_text_bg(callback: types.CallbackQuery, state: FSMContext):
    """Toggle semi-transparent backdrop box behind text."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    new_val = not data.get("text_bg", False)
    data["text_bg"] = new_val
    await state.update_data(text_bg=new_val)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:flip:toggle")
async def cb_toggle_flip(callback: types.CallbackQuery, state: FSMContext):
    """Toggle horizontal mirror of the image."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    new_val = not data.get("flip", False)
    data["flip"] = new_val
    await state.update_data(flip=new_val)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:crop:cycle")
async def cb_cycle_crop(callback: types.CallbackQuery, state: FSMContext):
    """Cycle center crop: off -> square -> 4:5."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    curr = data.get("crop", "off")
    idx = (CROP_CYCLES.index(curr) + 1) % len(CROP_CYCLES) if curr in CROP_CYCLES else 0
    new_crop = CROP_CYCLES[idx]
    data["crop"] = new_crop
    await state.update_data(crop=new_crop)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:bright:up" | F.data == "edit:bright:down")
async def cb_brightness(callback: types.CallbackQuery, state: FSMContext):
    """Adjust brightness in 0.1 steps (range 0.5 - 2.0)."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    cur = _clamp_float(data.get("brightness", 1.0), 0.5, 2.0, 1.0)
    step = 0.1 if callback.data.endswith(":up") else -0.1
    new_val = round(max(0.5, min(2.0, cur + step)), 1)
    data["brightness"] = new_val
    await state.update_data(brightness=new_val)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:contrast:up" | F.data == "edit:contrast:down")
async def cb_contrast(callback: types.CallbackQuery, state: FSMContext):
    """Adjust contrast in 0.1 steps (range 0.5 - 2.0)."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    cur = _clamp_float(data.get("contrast", 1.0), 0.5, 2.0, 1.0)
    step = 0.1 if callback.data.endswith(":up") else -0.1
    new_val = round(max(0.5, min(2.0, cur + step)), 1)
    data["contrast"] = new_val
    await state.update_data(contrast=new_val)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:undo")
async def cb_undo(callback: types.CallbackQuery, state: FSMContext):
    """Restore the previous editor state from the undo history."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    prev, remaining = pop_snapshot(data.get("history"))
    if prev is None:
        await callback.answer(t("editor.undo_empty", lang), show_alert=True)
        return
    # Full restore: clear first so keys added after the snapshot was taken
    # (e.g. "text" entered after the snapshot) are dropped too. Otherwise
    # update_data leaves them behind and the undo looks like it did nothing.
    # template_bytes is re-fetched from file_id on the next render.
    await state.clear()
    await state.update_data(history=remaining, **prev)
    await state.set_state(EditorSG.editing)
    fresh = await state.get_data()
    await callback.answer(t("editor.undone", lang))
    await _update_live_preview(callback, state, fresh, lang=lang)


@router.callback_query(F.data.startswith("edit:preset:"))
async def cb_apply_preset(callback: types.CallbackQuery, state: FSMContext):
    """Apply a one-tap style preset (layout + font + color + stroke + filter)."""
    lang = await _lang_of(callback.from_user.id)
    name = callback.data.rsplit(":", 1)[1]
    if name not in STYLE_PRESETS:
        await callback.answer(t("editor.err_preview", lang), show_alert=True)
        return
    data = await state.get_data()
    await _push_history(state, data)
    new_data = apply_preset(data, name)
    await state.update_data(**{k: v for k, v in new_data.items() if k in STYLE_PRESETS[name]})
    fresh = await state.get_data()
    await callback.answer(t("editor.preset_applied", lang))
    await _update_live_preview(callback, state, fresh, lang=lang)


@router.callback_query(F.data == "edit:shuffle")
async def cb_shuffle_style(callback: types.CallbackQuery, state: FSMContext):
    """Apply a random font + color + stroke + filter combo."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    combo = shuffled_style()
    data.update(combo)
    await state.update_data(**combo)
    await callback.answer(t("editor.shuffled", lang))
    await _update_live_preview(callback, state, data, lang=lang)


# ------------------------------------------------------------------------------
# Promotional Banner Controls (user-side opt-in banners)
# ------------------------------------------------------------------------------


def _banner_menu_kb(banners, lang: str) -> InlineKeyboardMarkup:
    """Build the banner picker submenu keyboard."""
    rows = []
    for b in banners:
        name = b.get("name") or f"Banner #{b['id']}"
        rows.append([
            InlineKeyboardButton(text=name, callback_data=f"edit:banner:apply:{b['id']}"),
            InlineKeyboardButton(text=t("editor.btn_dl", lang), callback_data=f"edit:banner:dl:{b['id']}"),
        ])
    rows.append([InlineKeyboardButton(text=t("editor.btn_remove_banner", lang), callback_data="edit:banner:remove")])
    rows.append([InlineKeyboardButton(text=t("editor.back_editor", lang), callback_data="edit:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "edit:banner:menu")
async def cb_banner_menu(callback: types.CallbackQuery, state: FSMContext):
    """Present opt-in promotional banner choices from the database."""
    lang = await _lang_of(callback.from_user.id)
    banners = await get_all_banners()
    if not banners:
        if is_admin(callback.from_user.id):
            msg = t("editor.banner_empty_admin", lang)
        else:
            msg = t("editor.no_banners", lang)
        await callback.answer(msg, show_alert=True)
        return

    data = await state.get_data()
    kb = _banner_menu_kb(banners, lang)
    await _show_submenu(callback, kb, t("editor.select_banner", lang))


@router.callback_query(F.data.startswith("edit:banner:apply:"))
async def cb_banner_apply(callback: types.CallbackQuery, state: FSMContext):
    """Attach the selected promotional banner and re-render the live preview."""
    lang = await _lang_of(callback.from_user.id)
    banner_id_str = callback.data.rsplit(":", 1)[1]
    if not banner_id_str.isdigit():
        await callback.answer(t("editor.err_invalid_banner", lang), show_alert=True)
        return

    banner = await get_banner_by_id(int(banner_id_str))
    if not banner or not banner.get("file_id"):
        await callback.answer(t("editor.banner_not_found", lang), show_alert=True)
        return

    banner_bytes = await _fetch_telegram_file_bytes(callback.bot, banner["file_id"])
    if not banner_bytes:
        await callback.answer(t("editor.err_banner_download", lang), show_alert=True)
        return

    data = await state.get_data()
    await _push_history(state, data)
    data["banner_id"] = banner["id"]
    await state.update_data(banner_id=banner["id"])
    await callback.answer(t("editor.banner_applied", lang))
    try:
        banners = await get_all_banners()
        kb = _banner_menu_kb(banners, lang)
        await _update_live_preview(callback, state, data, lang=lang, custom_kb=kb)
    except Exception as e:
        logger.warning("Banner apply preview failed: %s", e)
        await callback.answer(t("editor.err_banner_apply", lang), show_alert=True)


@router.callback_query(F.data == "edit:banner:remove")
async def cb_banner_remove(callback: types.CallbackQuery, state: FSMContext):
    """Detach the promotional banner and re-render the live preview."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    if not data.get("banner_id"):
        await callback.answer(
            t("editor.banner_not_applied", lang),
            show_alert=True,
        )
        return
    await _push_history(state, data)
    data["banner_id"] = None
    await state.update_data(banner_id=None)
    await callback.answer(t("editor.banner_removed", lang))
    try:
        await _update_live_preview(callback, state, data, lang=lang)
    except Exception as e:
        logger.warning("Banner remove preview failed: %s", e)
        await callback.answer(t("editor.err_banner_remove", lang), show_alert=True)


@router.callback_query(F.data.startswith("edit:banner:dl:"))
async def cb_banner_download(callback: types.CallbackQuery):
    """Send the banner file standalone (photo + document) without applying it to the meme."""
    lang = await _lang_of(callback.from_user.id)
    banner_id_str = callback.data.rsplit(":", 1)[1]
    if not banner_id_str.isdigit():
        await callback.answer(t("editor.err_invalid_banner", lang), show_alert=True)
        return

    banner = await get_banner_by_id(int(banner_id_str))
    if not banner or not banner.get("file_id"):
        await callback.answer(t("editor.banner_not_found", lang), show_alert=True)
        return

    name = banner.get("name") or f"Banner #{banner['id']}"
    await callback.answer(t("editor.sending_banner", lang))
    try:
        await callback.message.bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=banner["file_id"],
            caption=t("editor.banner_photo_cap", lang).format(name=name),
        )
        await callback.message.bot.send_document(
            chat_id=callback.message.chat.id,
            document=banner["file_id"],
            caption=t("editor.banner_doc_cap", lang).format(name=name),
        )
    except Exception as e:
        logger.warning("Banner download failed: %s", e)
        await callback.answer(t("editor.err_banner_send", lang), show_alert=True)


# ------------------------------------------------------------------------------
# Action Handlers (Export, Reset, Cancel, Change Text)
# ------------------------------------------------------------------------------

@router.callback_query(F.data == "edit:export:photo" | F.data == "ed_export_photo")
async def cb_export_photo(callback: types.CallbackQuery, state: FSMContext):
    """Export finalized meme as standard photo and reset state."""
    lang = await _lang_of(callback.from_user.id)
    await callback.answer(t("editor.exporting_photo", lang))
    data = await state.get_data()
    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        await callback.message.answer(t("editor.err_export", lang))
        return

    t_id = data.get("template_id")
    if t_id:
        await increment_template_usage(t_id)

    input_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_export.jpg")
    await callback.message.answer_photo(
        photo=input_file,
        caption=t("editor.export_done", lang),
    )
    await state.clear()


@router.callback_query(F.data == "edit:export:doc" | F.data == "ed_export_doc")
async def cb_export_doc(callback: types.CallbackQuery, state: FSMContext):
    """Export finalized meme as uncompressed lossless document and reset state."""
    lang = await _lang_of(callback.from_user.id)
    await callback.answer(t("editor.exporting_doc", lang))
    data = await state.get_data()
    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        await callback.message.answer(t("editor.err_export_doc", lang))
        return

    t_id = data.get("template_id")
    if t_id:
        await increment_template_usage(t_id)

    input_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_lossless.jpg")
    await callback.message.answer_document(
        document=input_file,
        caption=t("editor.export_doc_done", lang),
    )
    await state.clear()


@router.callback_query(F.data == "edit:reset" | F.data == "ed_reset")
async def cb_reset_editor(callback: types.CallbackQuery, state: FSMContext):
    """Revert modifications to baseline defaults."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    await _push_history(state, data)
    defaults = dict(
        variant="overlay",
        text_color="white",
        stroke_width=4,
        case_mode="raw",
        filter="none",
        is_clean=False,
        watermark_enabled=True,
        watermark_pos="bottom_right",
        watermark_scale=1.0,
        banner_id=None,
        text_offset_y=0,
        font_scale=1.0,
        text_align="center",
        stroke_color=None,
        text_bg=False,
        flip=False,
        crop="off",
        brightness=1.0,
        contrast=1.0,
    )
    data.update(defaults)
    await state.update_data(**defaults)
    await _update_live_preview(callback, state, data, lang=lang)


@router.callback_query(F.data == "edit:change_text" | F.data == "ed_change_text")
async def cb_change_text(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user to re-enter text."""
    lang = await _lang_of(callback.from_user.id)
    await state.set_state(EditorSG.waiting_for_text)
    await callback.answer()
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("editor.btn_cancel", lang), callback_data="edit:cancel")]]
    )
    await callback.message.answer(t("editor.send_new_text", lang), reply_markup=cancel_kb)


@router.callback_query(F.data == "edit:cancel" | F.data == "ed_cancel")
async def cb_cancel_editor(callback: types.CallbackQuery, state: FSMContext):
    """Cancel editing session and clear state."""
    lang = await _lang_of(callback.from_user.id)
    await state.clear()
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(t("editor.editor_closed", lang))


# ------------------------------------------------------------------------------
# Draft Save / Resume / Delete
# ------------------------------------------------------------------------------

# Only these editor state keys are persisted into / restored from a draft.
_DRAFT_KEYS = (
    "template_id",
    "file_id",
    "text",
    "variant",
    "text_color",
    "stroke_width",
    "case_mode",
    "filter",
    "font_key",
    "watermark_enabled",
    "watermark_pos",
    "watermark_scale",
    "watermark_file_id",
    "watermark_text",
    "watermark_opacity",
    "banner_id",
    "is_clean",
    "text_offset_y",
    "font_scale",
    "text_align",
    "stroke_color",
    "text_bg",
    "flip",
    "crop",
    "brightness",
    "contrast",
)


@router.callback_query(F.data == "edit:save_draft")
async def cb_save_draft(callback: types.CallbackQuery, state: FSMContext):
    """Persist the current editor state as a resumable draft (editing session is kept)."""
    lang = await _lang_of(callback.from_user.id)
    data = await state.get_data()
    if not data.get("file_id"):
        await callback.answer(t("editor.nothing_to_save", lang), show_alert=True)
        return
    clean = {k: v for k, v in data.items() if k in _DRAFT_KEYS}
    await save_draft(callback.from_user.id, clean)
    await callback.answer(t("editor.draft_saved", lang))


@router.message(Command("drafts"), StateFilter("*"), flags={"state": "*"})
async def handle_drafts_command(message: types.Message, state: FSMContext):
    """Show the user's saved draft with resume / delete actions."""
    lang = await _lang_of(message.from_user.id)
    await state.clear()
    draft = await get_draft(message.from_user.id)
    if not draft:
        await message.answer(t("editor.no_draft", lang))
        return

    text_snippet = (draft.get("text") or t("editor.no_text", lang))[:60]
    variant = _variant_label(str(draft.get("variant", "overlay")), lang)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("editor.resume_draft_btn", lang), callback_data="edit:draft:resume")],
            [InlineKeyboardButton(text=t("editor.delete_draft_btn", lang), callback_data="edit:draft:delete")],
        ]
    )
    await message.answer(
        f"{t('editor.draft_title', lang)}\n"
        f"{t('editor.draft_text', lang).format(snippet=text_snippet)}\n"
        f"{t('editor.draft_layout', lang).format(variant=variant)}",
        reply_markup=kb,
    )


@router.callback_query(F.data == "edit:draft:resume")
async def cb_draft_resume(callback: types.CallbackQuery, state: FSMContext):
    """Restore a saved draft into a fresh editor session with a new preview photo."""
    lang = await _lang_of(callback.from_user.id)
    draft = await get_draft(callback.from_user.id)
    if not draft:
        await callback.answer(t("editor.draft_not_found", lang), show_alert=True)
        return

    data = {k: v for k, v in draft.items() if k in _DRAFT_KEYS and v is not None}
    # DB stores the filter under `filter_name`; map it back to the editor's `filter` key.
    if "filter" not in data and draft.get("filter_name"):
        data["filter"] = draft["filter_name"]
    await state.clear()
    await state.update_data(**data)
    await state.set_state(EditorSG.editing)

    await callback.answer(t("editor.draft_resumed", lang))
    try:
        await callback.message.delete()
    except Exception:
        pass

    rendered_buf = await _render_current_draft(callback.bot, data, callback.from_user.id)
    if not rendered_buf:
        await callback.message.answer(t("editor.err_draft_render", lang))
        return

    input_file = BufferedInputFile(rendered_buf.getvalue(), filename="meme_preview.jpg")
    kb = get_editor_keyboard(data, lang)
    preview_msg = await callback.message.answer_photo(
        photo=input_file,
        caption=t("editor.live_preview", lang),
        reply_markup=kb,
    )
    await state.update_data(preview_message_id=preview_msg.message_id)


@router.callback_query(F.data == "edit:draft:delete")
async def cb_draft_delete(callback: types.CallbackQuery):
    """Delete the user's saved draft."""
    lang = await _lang_of(callback.from_user.id)
    await delete_draft(callback.from_user.id)
    await callback.answer(t("editor.draft_deleted", lang))
    try:
        await callback.message.delete()
    except Exception:
        pass
