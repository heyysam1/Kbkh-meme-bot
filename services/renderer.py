import asyncio
import io
import logging
import math
import textwrap
from pathlib import Path
from typing import Optional, Tuple, Union
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
import config
from services.effects import apply_filter
from services.font_manager import font_manager

logger = logging.getLogger("kbkh_meme_bot.renderer")

def load_font(font_path: Union[str, Path], size: int) -> ImageFont.FreeTypeFont:
    """Load font enforcing RAQM complex text-shaping engine with fallback."""
    try:
        return ImageFont.truetype(str(font_path), size=size, layout_engine=ImageFont.Layout.RAQM)
    except Exception as e:
        logger.warning(f"RAQM engine unavailable, falling back: {e}")
        return ImageFont.truetype(str(font_path), size=size)

# Security (CWE-409): Restrict decompression threshold to ~25 Megapixels (e.g. 5000x5000)
# to protect memory-constrained containers (512MB RAM) from OOM kill.
Image.MAX_IMAGE_PIXELS = 25_000_000

# Canonical Layout Variants
VARIANT_OVERLAY = "overlay"
VARIANT_TOP_BANNER = "top_banner"
VARIANT_BOTTOM_BANNER = "bottom_banner"
VARIANT_BREAKING_NEWS = "breaking_news"

# Color Presets Map
COLOR_PRESETS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "yellow": (255, 230, 0),
    "red": (255, 34, 34),
    "cyan": (0, 229, 255),
    "#ffffff": (255, 255, 255),
    "#000000": (0, 0, 0),
    "#ffe600": (255, 230, 0),
    "#ff2222": (255, 34, 34),
    "#00e5ff": (0, 229, 255),
}

def parse_color(color_val: Union[str, Tuple[int, int, int]]) -> Tuple[int, int, int]:
    """Parse color preset, hex string, or RGB tuple into (R, G, B)."""
    if isinstance(color_val, tuple) and len(color_val) >= 3:
        return (color_val[0], color_val[1], color_val[2])

    if isinstance(color_val, str):
        c_str = color_val.lower().strip()
        if c_str in COLOR_PRESETS:
            return COLOR_PRESETS[c_str]
        if c_str.startswith("#") and len(c_str) == 7:
            try:
                r = int(c_str[1:3], 16)
                g = int(c_str[3:5], 16)
                b = int(c_str[5:7], 16)
                return (r, g, b)
            except ValueError:
                pass

    return (255, 255, 255)

def _calculate_average_luminance(im_rgb: Image.Image, box: Tuple[int, int, int, int]) -> float:
    """
    Compute average luminance of pixels in the target crop box:
    L = 0.299*R + 0.587*G + 0.114*B.
    """
    crop = im_rgb.crop(box)
    pixels = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
    if not pixels:
        return 128.0

    total_lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels)
    return total_lum / len(pixels)

def _apply_casing(text: str, case_mode: str) -> str:
    """Apply case transformation: 'raw', 'upper', or 'title'."""
    mode = case_mode.lower().strip()
    if mode in ("upper", "uppercase"):
        return text.upper()
    elif mode in ("title", "titlecase"):
        return text.title()
    return text

def _draw_stroked_text(draw: ImageDraw.ImageDraw, xy, text, font, fill,
                      stroke_width: int = 0, stroke_fill=None, **kwargs):
    """Draw multiline text with manual outline stroke.

    PIL's built-in stroke_width breaks RAQM complex text shaping (Bengali
    renders as broken glyphs), so the outline is drawn manually with offset
    copies. This preserves correct Bengali shaping.
    """
    if stroke_width and stroke_width > 0 and stroke_fill is not None:
        x, y = xy
        sw = int(stroke_width)
        for dx in range(-sw, sw + 1):
            for dy in range(-sw, sw + 1):
                if dx * dx + dy * dy <= sw * sw and (dx, dy) != (0, 0):
                    draw.multiline_text((x + dx, y + dy), text, font=font,
                                        fill=stroke_fill, **kwargs)
    draw.multiline_text(xy, text, font=font, fill=fill, **kwargs)


def _wrap_text_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> str:
    """
    Wrap text so each line fits within max_width using multiline_textbbox.
    Handles English word breaks and Bengali scripts cleanly.
    """
    words = text.split()
    if not words:
        return text

    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.multiline_textbbox((0, 0), test_line, font=font)
        line_w = bbox[2] - bbox[0]

        if line_w <= max_width or not current_line:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]

    if current_line:
        lines.append(" ".join(current_line))

    return "\n".join(lines)

def _fit_text(
    text: str,
    font_path: Path,
    initial_size: int,
    max_width: int,
    max_height: int,
    draw: ImageDraw.ImageDraw,
    min_size: int = 14,
) -> Tuple[str, ImageFont.FreeTypeFont, int, int]:
    """
    Iterative decrement loop reducing font size until the multi-line text
    fits within both max_width and max_height safely.
    Includes safeguards against runaway CPU loops.
    """
    words = text.split()
    if len(words) > 100:
        text = " ".join(words[:100])

    current_size = initial_size
    max_iterations = 35
    iteration = 0

    while current_size >= min_size and iteration < max_iterations:
        iteration += 1
        font = load_font(font_path, current_size)
        wrapped = _wrap_text_to_width(text, font, max_width, draw)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=int(current_size * 0.2))
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_h <= max_height and text_w <= max_width:
            return wrapped, font, text_w, text_h

        step = 4 if current_size > 44 else 2
        current_size -= step

    font = load_font(font_path, min_size)
    wrapped = _wrap_text_to_width(text, font, max_width, draw)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=int(min_size * 0.2))
    return wrapped, font, bbox[2] - bbox[0], bbox[3] - bbox[1]

def _sync_render_worker(
    template_bytes: bytes,
    text: str,
    font_path: Path,
    variant: str = "overlay",
    is_clean: bool = False,
    text_color: str = "white",
    stroke_width: int = 4,
    filter_name: str = "none",
    case_mode: str = "raw",
    watermark_bytes: Optional[bytes] = None,
    watermark_text: Optional[str] = None,
    watermark_pos: str = "bottom_right",
    watermark_scale: float = 1.0,
    watermark_opacity: float = 0.8,
    watermark_enabled: bool = True,
    banner_bytes: Optional[bytes] = None,
    text_offset_y: int = 0,
    font_scale: float = 1.0,
    text_align: str = "center",
    stroke_color: Optional[str] = None,
    text_bg: bool = False,
    flip: bool = False,
    crop: str = "off",
    brightness: float = 1.0,
    contrast: float = 1.0,
) -> io.BytesIO:
    """
    Synchronous rendering core for KBKH Meme Generator running inside worker thread.
    Zero local disk writing; executes strictly in-memory.
    """
    # 1. Load base template
    try:
        base_image = Image.open(io.BytesIO(template_bytes)).convert("RGB")
    except Exception as e:
        raise ValueError("Unsupported template media: not a readable image") from e

    # 1b. Horizontal mirror (before any other transform)
    if flip:
        base_image = ImageOps.mirror(base_image)

    # 1c. Center crop to the requested aspect ratio (before downscale)
    crop_key = (crop or "off").lower().strip()
    if crop_key in ("square", "1:1", "4:5"):
        target_aspect = 1.0 if crop_key in ("square", "1:1") else 0.8
        cw, ch = base_image.size
        if cw / max(1, ch) > target_aspect:
            new_w = max(1, int(ch * target_aspect))
            x0 = (cw - new_w) // 2
            base_image = base_image.crop((x0, 0, x0 + new_w, ch))
        else:
            new_h = max(1, int(cw / target_aspect))
            y0 = (ch - new_h) // 2
            base_image = base_image.crop((0, y0, cw, y0 + new_h))

    w, h = base_image.size

    # Downscale excessively large template images to prevent container OOM
    MAX_CANVAS_DIM = 2560
    if w > MAX_CANVAS_DIM or h > MAX_CANVAS_DIM:
        base_image.thumbnail((MAX_CANVAS_DIM, MAX_CANVAS_DIM), Image.Resampling.LANCZOS)
        w, h = base_image.size

    # 1d. Brightness / contrast adjustments
    try:
        b_val = max(0.5, min(2.0, float(brightness)))
    except (TypeError, ValueError):
        b_val = 1.0
    try:
        c_val = max(0.5, min(2.0, float(contrast)))
    except (TypeError, ValueError):
        c_val = 1.0
    if b_val != 1.0:
        base_image = ImageEnhance.Brightness(base_image).enhance(b_val)
    if c_val != 1.0:
        base_image = ImageEnhance.Contrast(base_image).enhance(c_val)

    # Apply Visual Effect / Filter
    base_image = apply_filter(base_image, filter_name)

    # Padding and text sizing calculations (font_scale multiplies auto-fit size)
    padding = max(16, round(w * 0.025))
    max_text_width = w - (2 * padding)
    try:
        fs = max(0.5, min(2.0, float(font_scale)))
    except (TypeError, ValueError):
        fs = 1.0
    initial_font_size = max(24, min(96, round(w * 0.055 * fs)))

    # Text vertical nudge (px, clamped to half the canvas height)
    try:
        y_off = int(text_offset_y or 0)
    except (TypeError, ValueError):
        y_off = 0
    y_off = max(-h // 2, min(h // 2, y_off))

    # Text alignment: left / center / right
    align_key = (text_align or "center").lower().strip()
    if align_key not in ("left", "center", "right"):
        align_key = "center"

    # Semi-transparent backdrop box behind each text block
    def _with_text_bg(canvas_img: Image.Image, box: Tuple[int, int, int, int]) -> Image.Image:
        overlay = Image.new("RGBA", canvas_img.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        x0, y0, x1, y1 = box
        pad = max(6, round(w * 0.012))
        odraw.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill=(0, 0, 0, 140))
        return Image.alpha_composite(canvas_img.convert("RGBA"), overlay).convert("RGB")

    def _seg_x(seg_w: int) -> int:
        if align_key == "left":
            return padding
        if align_key == "right":
            return w - seg_w - padding
        return (w - seg_w) // 2

    # Dummy canvas for measurement
    dummy_img = Image.new("RGB", (10, 10))
    dummy_draw = ImageDraw.Draw(dummy_img)

    # Clean and case-transform text
    cleaned_text = _apply_casing(text.strip() if text else "...", case_mode)
    if not cleaned_text:
        cleaned_text = "..."

    # Parse colors and stroke parameters
    parsed_text_color = parse_color(text_color)
    # A non-default color choice must survive layout variants that otherwise
    # force their own text color (top_banner, breaking_news).
    custom_color = text_color.strip().lower() not in ("white", "#ffffff")
    is_bright_text = (0.299 * parsed_text_color[0] + 0.587 * parsed_text_color[1] + 0.114 * parsed_text_color[2]) >= 128
    default_stroke_fill = (0, 0, 0) if is_bright_text else (255, 255, 255)
    # Explicit stroke color choice overrides the automatic contrast pick.
    if stroke_color:
        try:
            stroke_fill = parse_color(stroke_color)
        except Exception:
            stroke_fill = default_stroke_fill
    else:
        stroke_fill = default_stroke_fill

    variant_key = variant.lower().strip()

    # --------------------------------------------------------------------------
    # Layout Variants
    # --------------------------------------------------------------------------

    if variant_key in ("overlay", "classic_overlay", "variant_c"):
        # Variant: Classic Overlay directly on the canvas
        canvas = base_image.copy()
        draw = ImageDraw.Draw(canvas)

        parts = [p.strip() for p in cleaned_text.split("|", 1)]
        top_text = parts[0]
        bottom_text = parts[1] if len(parts) > 1 else ""

        max_seg_height = round(h * 0.28)

        if top_text:
            wrapped_top, top_font, tw, th = _fit_text(
                top_text, font_path, initial_font_size, max_text_width, max_seg_height, dummy_draw
            )
            top_x = _seg_x(tw)
            top_y = padding + y_off
            if text_bg:
                canvas = _with_text_bg(canvas, (top_x, top_y, top_x + tw, top_y + th))
                draw = ImageDraw.Draw(canvas)

            # Auto-halo / drop-shadow if stroke is 0
            if stroke_width == 0:
                draw.multiline_text(
                    (top_x + 2, top_y + 2),
                    wrapped_top,
                    font=top_font,
                    fill=(0, 0, 0) if is_bright_text else (255, 255, 255),
                    align=align_key,
                    spacing=int(top_font.size * 0.2),
                )

            _draw_stroked_text(
                draw,
                (top_x, top_y),
                wrapped_top,
                font=top_font,
                fill=parsed_text_color,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
                align=align_key,
                spacing=int(top_font.size * 0.2),
            )

        if bottom_text:
            wrapped_bot, bot_font, bw, bh = _fit_text(
                bottom_text, font_path, initial_font_size, max_text_width, max_seg_height, dummy_draw
            )
            bot_x = _seg_x(bw)
            bot_y = h - bh - padding + y_off
            if text_bg:
                canvas = _with_text_bg(canvas, (bot_x, bot_y, bot_x + bw, bot_y + bh))
                draw = ImageDraw.Draw(canvas)

            if stroke_width == 0:
                draw.multiline_text(
                    (bot_x + 2, bot_y + 2),
                    wrapped_bot,
                    font=bot_font,
                    fill=(0, 0, 0) if is_bright_text else (255, 255, 255),
                    align=align_key,
                    spacing=int(bot_font.size * 0.2),
                )

            _draw_stroked_text(
                draw,
                (bot_x, bot_y),
                wrapped_bot,
                font=bot_font,
                fill=parsed_text_color,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
                align=align_key,
                spacing=int(bot_font.size * 0.2),
            )

    elif variant_key in ("bottom_banner", "bottom"):
        # Variant: Bottom Banner extending canvas downward
        max_banner_height = round(h * 0.45)
        wrapped_text, font, tw, th = _fit_text(
            cleaned_text, font_path, initial_font_size, max_text_width, max_banner_height, dummy_draw
        )

        banner_height = th + (2 * padding)
        total_height = h + banner_height

        # Bottom banner background matches inverse of text color
        banner_bg = (26, 26, 26) if is_bright_text else (255, 255, 255)

        canvas = Image.new("RGB", (w, total_height), banner_bg)
        canvas.paste(base_image, (0, 0))
        draw = ImageDraw.Draw(canvas)

        text_x = _seg_x(tw)
        text_y = h + padding + y_off
        if text_bg:
            canvas = _with_text_bg(canvas, (text_x, text_y, text_x + tw, text_y + th))
            draw = ImageDraw.Draw(canvas)
        _draw_stroked_text(
            draw,
            (text_x, text_y),
            wrapped_text,
            font=font,
            fill=parsed_text_color,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            align=align_key,
            spacing=int(font.size * 0.2),
        )

    elif variant_key in ("breaking_news", "news", "breaking"):
        # Variant: Breaking News Ticker overlay at bottom
        canvas = base_image.copy()
        draw = ImageDraw.Draw(canvas)

        ticker_h = max(60, round(h * 0.16))
        ticker_y = h - ticker_h

        # Dark ticker background with red header bar
        ticker_bg = Image.new("RGBA", (w, ticker_h), (20, 20, 20, 240))
        canvas.paste(ticker_bg.convert("RGB"), (0, ticker_y))

        # Red label box
        label_w = max(110, round(w * 0.28))
        label_bar = Image.new("RGB", (label_w, ticker_h), (210, 20, 20))
        canvas.paste(label_bar, (0, ticker_y))

        # Draw "BREAKING" or "NEWS" text on badge
        badge_font = load_font(font_path, max(16, round(ticker_h * 0.38)))
        draw.text((12, ticker_y + round(ticker_h * 0.28)), "BREAKING", font=badge_font, fill=(255, 255, 255))

        # Ticker text area
        ticker_max_w = w - label_w - 20
        wrapped_news, news_font, nw, nh = _fit_text(
            cleaned_text, font_path, max(18, round(ticker_h * 0.40)), ticker_max_w, ticker_h - 10, dummy_draw
        )
        news_x = label_w + 14
        if align_key == "right":
            news_x = w - nw - 14
        elif align_key == "center":
            news_x = label_w + 14 + max(0, (ticker_max_w - nw) // 2)
        news_y = ticker_y + (ticker_h - nh) // 2 + y_off
        if text_bg:
            canvas = _with_text_bg(canvas, (news_x, news_y, news_x + nw, news_y + nh))
            draw = ImageDraw.Draw(canvas)
        _draw_stroked_text(
            draw,
            (news_x, news_y),
            wrapped_news,
            font=news_font,
            fill=parsed_text_color if custom_color else (255, 230, 0),
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            align=align_key,
        )

    else:
        # Variant: Top Banner (Default White/Dark Header extending canvas upward)
        is_dark = variant_key in ("dark_header", "dark", "variant_b")
        header_bg_color = (26, 26, 26) if is_dark else (255, 255, 255)
        text_c = parsed_text_color if custom_color else ((255, 255, 255) if is_dark else (0, 0, 0))

        max_header_height = round(h * 0.45)
        wrapped_text, font, tw, th = _fit_text(
            cleaned_text, font_path, initial_font_size, max_text_width, max_header_height, dummy_draw
        )

        header_height = th + (2 * padding)
        total_height = header_height + h

        canvas = Image.new("RGB", (w, total_height), header_bg_color)
        draw = ImageDraw.Draw(canvas)

        text_x = _seg_x(tw)
        text_y = padding + y_off
        if text_bg:
            canvas = _with_text_bg(canvas, (text_x, text_y, text_x + tw, text_y + th))
            draw = ImageDraw.Draw(canvas)
        _draw_stroked_text(
            draw,
            (text_x, text_y),
            wrapped_text,
            font=font,
            fill=text_c,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            align=align_key,
            spacing=int(font.size * 0.2),
        )

        canvas.paste(base_image, (0, header_height))

    # --------------------------------------------------------------------------
    # Brand Identity (KBKH Group Smart Contrast)
    # --------------------------------------------------------------------------
    if not is_clean:
        logo_w = max(80, round(w * 0.12))
        logo_h = max(24, round(logo_w * (324 / 1080)))
        logo_margin = max(10, round(w * 0.02))

        logo_box = (
            w - logo_margin - logo_w,
            logo_margin,
            w - logo_margin,
            logo_margin + logo_h,
        )

        lum = _calculate_average_luminance(canvas, logo_box)
        logo_path = config.WHITE_LOGO_PATH if lum < config.LUMINANCE_THRESHOLD else config.BLACK_LOGO_PATH

        if logo_path.exists():
            with Image.open(logo_path) as raw_logo:
                logo_rgba = raw_logo.convert("RGBA").resize((logo_w, logo_h), Image.Resampling.LANCZOS)
                alpha = logo_rgba.split()[3].point(lambda p: int(p * 0.80))
                logo_rgba.putalpha(alpha)
                canvas.paste(logo_rgba, (logo_box[0], logo_box[1]), mask=logo_rgba)

    # --------------------------------------------------------------------------
    # Comprehensive Watermark Suite
    # --------------------------------------------------------------------------
    if watermark_enabled and (watermark_bytes or watermark_text):
        diag = math.sqrt(canvas.width ** 2 + canvas.height ** 2)
        base_target_w = round(diag * 0.08 * watermark_scale)
        clamped_wm_w = max(40, min(round(canvas.width * 0.40), base_target_w))
        m = max(12, round(canvas.width * 0.02))
        raw_pos_key = watermark_pos.lower().strip()
        pos_shortcuts = {"tl": "top_left", "tr": "top_right", "bl": "bottom_left", "br": "bottom_right", "bc": "bottom_center"}
        pos_key = pos_shortcuts.get(raw_pos_key, raw_pos_key)

        if watermark_bytes:
            try:
                wm_img = Image.open(io.BytesIO(watermark_bytes)).convert("RGBA")
                if wm_img.width > 2048 or wm_img.height > 2048:
                    wm_img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)

                aspect = wm_img.height / max(1, wm_img.width)
                target_h = max(20, round(clamped_wm_w * aspect))
                wm_resized = wm_img.resize((clamped_wm_w, target_h), Image.Resampling.LANCZOS)

                # Apply opacity multiplier
                r, g, b, a = wm_resized.split()
                alpha_factor = min(1.0, max(0.1, watermark_opacity))
                a = a.point(lambda p: int(p * alpha_factor))
                wm_resized.putalpha(a)

                cw, ch = canvas.size
                if pos_key == "top_left":
                    pos_xy = (m, m)
                elif pos_key == "top_right":
                    pos_xy = (cw - clamped_wm_w - m, m)
                elif pos_key == "bottom_left":
                    pos_xy = (m, ch - target_h - m)
                elif pos_key == "bottom_center":
                    pos_xy = ((cw - clamped_wm_w) // 2, ch - target_h - m)
                else:  # bottom_right
                    pos_xy = (cw - clamped_wm_w - m, ch - target_h - m)

                canvas.paste(wm_resized, pos_xy, mask=wm_resized)
            except Exception:
                logger.warning("Watermark image overlay failed; continuing without watermark.", exc_info=True)
                pass

        elif watermark_text:
            try:
                draw = ImageDraw.Draw(canvas)
                wm_font_size = max(14, round(clamped_wm_w * 0.15))
                wm_font = font_manager.load_font(font_path, wm_font_size)
                w_box = draw.textbbox((0, 0), watermark_text, font=wm_font)
                wt_w = w_box[2] - w_box[0]
                wt_h = w_box[3] - w_box[1]

                cw, ch = canvas.size
                if pos_key == "top_left":
                    pos_xy = (m, m)
                elif pos_key == "top_right":
                    pos_xy = (cw - wt_w - m, m)
                elif pos_key == "bottom_left":
                    pos_xy = (m, ch - wt_h - m)
                elif pos_key == "bottom_center":
                    pos_xy = ((cw - wt_w) // 2, ch - wt_h - m)
                else:  # bottom_right
                    pos_xy = (cw - wt_w - m, ch - wt_h - m)

                # Draw subtle watermark text with outline (manual stroke preserves Bengali shaping)
                _draw_stroked_text(draw, pos_xy, watermark_text, font=wm_font,
                                   fill=(255, 255, 255, int(255 * watermark_opacity)),
                                   stroke_width=2, stroke_fill=(0, 0, 0))
            except Exception:
                logger.warning("Watermark text overlay failed; continuing without watermark.", exc_info=True)
                pass

    # --------------------------------------------------------------------------
    # Opt-In Promotional Banner Extension (position: top / bottom / breaking)
    # --------------------------------------------------------------------------
    if banner_bytes is not None:
        try:
            banner_img = Image.open(io.BytesIO(banner_bytes)).convert("RGB")
            if banner_img.width > 2560 or banner_img.height > 1024:
                banner_img.thumbnail((2560, 1024), Image.Resampling.LANCZOS)

            bw, bh = banner_img.size
            if bw > 0 and bh > 0:
                scale_ratio = canvas.width / bw
                new_banner_h = max(30, round(bh * scale_ratio))
                banner_resized = banner_img.resize((canvas.width, new_banner_h), Image.Resampling.LANCZOS)

                current_w, current_h = canvas.size
                # Promo banner always goes at the bottom.
                extended_canvas = Image.new("RGB", (current_w, current_h + new_banner_h))
                extended_canvas.paste(canvas, (0, 0))
                extended_canvas.paste(banner_resized, (0, current_h))
                canvas = extended_canvas
        except Exception:
            logger.warning("Promotional banner extension failed; continuing without banner.", exc_info=True)
            pass

    # Export buffer
    output_buffer = io.BytesIO()
    canvas.save(output_buffer, format="JPEG", quality=90, optimize=True)
    output_buffer.seek(0)
    return output_buffer

async def render_meme(
    template_bytes: bytes,
    text: str,
    font_path: Path,
    variant: str = "overlay",
    is_clean: bool = False,
    text_color: str = "white",
    stroke_width: int = 4,
    filter_name: str = "none",
    case_mode: str = "raw",
    watermark_bytes: Optional[bytes] = None,
    watermark_text: Optional[str] = None,
    watermark_pos: str = "bottom_right",
    watermark_scale: float = 1.0,
    watermark_opacity: float = 0.8,
    watermark_enabled: bool = True,
    banner_bytes: Optional[bytes] = None,
    text_offset_y: int = 0,
    font_scale: float = 1.0,
    text_align: str = "center",
    stroke_color: Optional[str] = None,
    text_bg: bool = False,
    flip: bool = False,
    crop: str = "off",
    brightness: float = 1.0,
    contrast: float = 1.0,
) -> io.BytesIO:
    """
    Public asynchronous entry point for meme rendering.
    Safely offloads heavy Pillow image processing to worker threads.
    """
    return await asyncio.to_thread(
        _sync_render_worker,
        template_bytes,
        text,
        font_path,
        variant,
        is_clean,
        text_color,
        stroke_width,
        filter_name,
        case_mode,
        watermark_bytes,
        watermark_text,
        watermark_pos,
        watermark_scale,
        watermark_opacity,
        watermark_enabled,
        banner_bytes,
        text_offset_y,
        font_scale,
        text_align,
        stroke_color,
        text_bg,
        flip,
        crop,
        brightness,
        contrast,
    )
