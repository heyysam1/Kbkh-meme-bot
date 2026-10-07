import asyncio
import io
import math
import textwrap
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
import config
from services.font_manager import font_manager

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
    """
    current_size = initial_size

    while current_size >= min_size:
        font = font_manager.load_font(font_path, current_size)
        wrapped = _wrap_text_to_width(text, font, max_width, draw)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=int(current_size * 0.2))
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_h <= max_height and text_w <= max_width:
            return wrapped, font, text_w, text_h

        current_size -= 2

    # Return minimum size result with truncation if still overflowing
    font = font_manager.load_font(font_path, min_size)
    wrapped = _wrap_text_to_width(text, font, max_width, draw)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=int(min_size * 0.2))
    return wrapped, font, bbox[2] - bbox[0], bbox[3] - bbox[1]

def _sync_render_worker(
    template_bytes: bytes,
    text: str,
    font_path: Path,
    variant: str = "white_header",
    is_clean: bool = False,
    watermark_bytes: Optional[bytes] = None,
    watermark_pos: str = "bottom_right",
    banner_bytes: Optional[bytes] = None,
) -> io.BytesIO:
    """
    Synchronous rendering core for KBKH Meme Generator running inside worker thread.
    Zero local disk writing; executes strictly in-memory.
    """
    # 1. Load base template
    base_image = Image.open(io.BytesIO(template_bytes)).convert("RGB")
    w, h = base_image.size

    padding = max(16, round(w * 0.025))
    max_text_width = w - (2 * padding)
    initial_font_size = max(24, min(96, round(w * 0.055)))

    # Dummy canvas for measurement
    dummy_img = Image.new("RGB", (10, 10))
    dummy_draw = ImageDraw.Draw(dummy_img)

    # 2. Text layout & Variant Rendering
    cleaned_text = text.strip()
    if not cleaned_text:
        cleaned_text = "..."

    # Normalize variant key
    variant_key = variant.lower().strip()

    if variant_key == "classic_overlay":
        # Variant C: Classic Impact Overlay on template canvas
        canvas = base_image.copy()
        draw = ImageDraw.Draw(canvas)

        # Check for top and bottom split via '|'
        parts = [p.strip() for p in cleaned_text.split("|", 1)]
        top_text = parts[0]
        bottom_text = parts[1] if len(parts) > 1 else ""

        max_seg_height = round(h * 0.28)

        # Render Top Segment
        if top_text:
            wrapped_top, top_font, tw, th = _fit_text(
                top_text, font_path, initial_font_size, max_text_width, max_seg_height, dummy_draw
            )
            top_x = (w - tw) // 2
            top_y = padding
            stroke_w = max(2, round(top_font.size * 0.06))
            draw.multiline_text(
                (top_x, top_y),
                wrapped_top,
                font=top_font,
                fill=(255, 255, 255),
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0),
                align="center",
                spacing=int(top_font.size * 0.2),
            )

        # Render Bottom Segment
        if bottom_text:
            wrapped_bot, bot_font, bw, bh = _fit_text(
                bottom_text, font_path, initial_font_size, max_text_width, max_seg_height, dummy_draw
            )
            bot_x = (w - bw) // 2
            bot_y = h - bh - padding
            stroke_w = max(2, round(bot_font.size * 0.06))
            draw.multiline_text(
                (bot_x, bot_y),
                wrapped_bot,
                font=bot_font,
                fill=(255, 255, 255),
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0),
                align="center",
                spacing=int(bot_font.size * 0.2),
            )

    else:
        # Variant A (White Header) or Variant B (Dark Header)
        is_dark = variant_key in ("dark_header", "dark", "variant_b")
        header_bg_color = (26, 26, 26) if is_dark else (255, 255, 255)
        text_color = (255, 255, 255) if is_dark else (0, 0, 0)

        max_header_height = round(h * 0.45)
        wrapped_text, font, tw, th = _fit_text(
            cleaned_text, font_path, initial_font_size, max_text_width, max_header_height, dummy_draw
        )

        header_height = th + (2 * padding)
        total_height = header_height + h

        # Create combined canvas
        canvas = Image.new("RGB", (w, total_height), header_bg_color)
        draw = ImageDraw.Draw(canvas)

        # Draw wrapped text centered horizontally in header
        text_x = (w - tw) // 2
        text_y = padding
        draw.multiline_text(
            (text_x, text_y),
            wrapped_text,
            font=font,
            fill=text_color,
            align="center",
            spacing=int(font.size * 0.2),
        )

        # Paste base meme template directly below header
        canvas.paste(base_image, (0, header_height))

    # 3. Brand Identity & Smart Contrast Logo Placement (KBKH Group)
    if not is_clean:
        logo_w = max(80, round(w * 0.12))
        logo_h = max(24, round(logo_w * (324 / 1080)))
        logo_margin = max(10, round(w * 0.02))

        # Position at top-right corner
        logo_box = (
            w - logo_margin - logo_w,
            logo_margin,
            w - logo_margin,
            logo_margin + logo_h,
        )

        # Sample luminance
        lum = _calculate_average_luminance(canvas, logo_box)
        logo_path = config.WHITE_LOGO_PATH if lum < config.LUMINANCE_THRESHOLD else config.BLACK_LOGO_PATH

        if logo_path.exists():
            with Image.open(logo_path) as raw_logo:
                logo_rgba = raw_logo.convert("RGBA").resize((logo_w, logo_h), Image.Resampling.LANCZOS)
                # Apply 80% opacity
                alpha = logo_rgba.split()[3].point(lambda p: int(p * 0.80))
                logo_rgba.putalpha(alpha)
                # Paste using mask
                canvas.paste(logo_rgba, (logo_box[0], logo_box[1]), mask=logo_rgba)

    # 4. User Custom Watermark Injection
    if watermark_bytes:
        try:
            wm_img = Image.open(io.BytesIO(watermark_bytes)).convert("RGBA")
            wm_w = max(50, round(w * 0.09))
            wm_h = max(20, round(wm_img.height * (wm_w / wm_img.width)))
            wm_resized = wm_img.resize((wm_w, wm_h), Image.Resampling.LANCZOS)

            m = max(12, round(w * 0.02))
            pos_key = watermark_pos.lower().strip()

            if pos_key == "top_left":
                pos_xy = (m, m)
            elif pos_key == "bottom_left":
                pos_xy = (m, canvas.height - wm_h - m)
            elif pos_key == "center":
                pos_xy = ((w - wm_w) // 2, (canvas.height - wm_h) // 2)
            else:  # bottom_right default
                pos_xy = (w - wm_w - m, canvas.height - wm_h - m)

            canvas.paste(wm_resized, pos_xy, mask=wm_resized)
        except Exception:
            pass  # Fail gracefully if custom watermark is corrupt

    # 5. Strict Opt-In Promotional Banner Extension (Bannerless by Default)
    if banner_bytes is not None:
        try:
            banner_img = Image.open(io.BytesIO(banner_bytes)).convert("RGB")
            bw, bh = banner_img.size
            if bw > 0 and bh > 0:
                scale_ratio = w / bw
                new_banner_h = max(30, round(bh * scale_ratio))
                banner_resized = banner_img.resize((w, new_banner_h), Image.Resampling.LANCZOS)

                current_w, current_h = canvas.size
                extended_canvas = Image.new("RGB", (current_w, current_h + new_banner_h))
                extended_canvas.paste(canvas, (0, 0))
                extended_canvas.paste(banner_resized, (0, current_h))
                canvas = extended_canvas
        except Exception:
            pass  # Fail gracefully if banner bytes are invalid

    # 6. In-Memory Export (JPEG 90% quality)
    output_buffer = io.BytesIO()
    canvas.save(output_buffer, format="JPEG", quality=90, optimize=True)
    output_buffer.seek(0)
    return output_buffer

async def render_meme(
    template_bytes: bytes,
    text: str,
    font_path: Path,
    variant: str = "white_header",
    is_clean: bool = False,
    watermark_bytes: Optional[bytes] = None,
    watermark_pos: str = "bottom_right",
    banner_bytes: Optional[bytes] = None,
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
        watermark_bytes,
        watermark_pos,
        banner_bytes,
    )
