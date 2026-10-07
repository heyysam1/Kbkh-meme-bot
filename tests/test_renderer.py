import io
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
import config
from services.font_manager import font_manager
from services.effects import apply_filter
from services.renderer import (
    _calculate_average_luminance,
    _wrap_text_to_width,
    _sync_render_worker,
    VARIANT_OVERLAY,
    VARIANT_TOP_BANNER,
    VARIANT_BOTTOM_BANNER,
    VARIANT_BREAKING_NEWS,
)


class TestRenderer(unittest.TestCase):
    def _create_sample_image(self, width: int = 400, height: int = 300, color: tuple = (200, 200, 200)) -> bytes:
        """Helper to generate an in-memory sample RGB JPEG byte buffer."""
        buf = io.BytesIO()
        img = Image.new("RGB", (width, height), color=color)
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_calculate_average_luminance(self):
        # Pure black image should have luminance ~0
        black_img = Image.new("RGB", (100, 100), (0, 0, 0))
        lum_black = _calculate_average_luminance(black_img, (0, 0, 100, 100))
        self.assertEqual(lum_black, 0.0)

        # Pure white image should have luminance ~255
        white_img = Image.new("RGB", (100, 100), (255, 255, 255))
        lum_white = _calculate_average_luminance(white_img, (0, 0, 100, 100))
        self.assertAlmostEqual(lum_white, 255.0, places=1)

        # Test formula: 0.299*R + 0.587*G + 0.114*B
        red_img = Image.new("RGB", (10, 10), (255, 0, 0))
        lum_red = _calculate_average_luminance(red_img, (0, 0, 10, 10))
        self.assertAlmostEqual(lum_red, 255 * 0.299, places=1)

    def test_wrap_text_to_width(self):
        font_path = font_manager.get_font_path(script="english")
        font = font_manager.load_font(font_path, 20)
        dummy_img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(dummy_img)

        long_text = "This is a very long text string intended to test dynamic multiline wrapping functionality"
        wrapped = _wrap_text_to_width(long_text, font, max_width=200, draw=draw)
        self.assertIn("\n", wrapped)
        for line in wrapped.split("\n"):
            bbox = draw.multiline_textbbox((0, 0), line, font=font)
            self.assertTrue((bbox[2] - bbox[0]) <= 200 or len(line.split()) == 1)

    def test_all_layout_variants(self):
        template_bytes = self._create_sample_image(500, 400)
        font_path = font_manager.get_font_path(script="bengali")

        # 1. Overlay
        buf_ov = _sync_render_worker(
            template_bytes=template_bytes,
            text="শীর্ষ টেক্সট | নিচের টেক্সট",
            font_path=font_path,
            variant=VARIANT_OVERLAY,
            is_clean=True,
            text_color="yellow",
            stroke_width=5,
        )
        self.assertIsInstance(buf_ov, io.BytesIO)
        img_ov = Image.open(buf_ov)
        self.assertEqual(img_ov.size, (500, 400))

        # 2. Top Banner
        buf_tb = _sync_render_worker(
            template_bytes=template_bytes,
            text="Top Banner Caption Line",
            font_path=font_path,
            variant=VARIANT_TOP_BANNER,
            is_clean=True,
        )
        img_tb = Image.open(buf_tb)
        self.assertEqual(img_tb.size[0], 500)
        self.assertGreater(img_tb.size[1], 400)

        # 3. Bottom Banner
        buf_bb = _sync_render_worker(
            template_bytes=template_bytes,
            text="Bottom Banner Caption Line",
            font_path=font_path,
            variant=VARIANT_BOTTOM_BANNER,
            is_clean=True,
        )
        img_bb = Image.open(buf_bb)
        self.assertEqual(img_bb.size[0], 500)
        self.assertGreater(img_bb.size[1], 400)

        # 4. Breaking News
        buf_bn = _sync_render_worker(
            template_bytes=template_bytes,
            text="BREAKING: HIGH PRECISION MEME RENDERED",
            font_path=font_path,
            variant=VARIANT_BREAKING_NEWS,
            is_clean=True,
        )
        img_bn = Image.open(buf_bn)
        self.assertEqual(img_bn.size, (500, 400))

    def test_casing_and_colors(self):
        template_bytes = self._create_sample_image(400, 300)
        font_path = font_manager.get_font_path(script="english")

        for color_name in ["white", "black", "yellow", "red", "cyan"]:
            buf = _sync_render_worker(
                template_bytes=template_bytes,
                text="color test",
                font_path=font_path,
                variant=VARIANT_OVERLAY,
                text_color=color_name,
                case_mode="upper",
                is_clean=True,
            )
            self.assertIsInstance(buf, io.BytesIO)

    def test_canvas_filters(self):
        img = Image.new("RGB", (100, 100), (120, 80, 200))

        # Deepfry
        fry = apply_filter(img, "deepfry")
        self.assertEqual(fry.size, (100, 100))

        # Grayscale
        gray = apply_filter(img, "grayscale")
        self.assertEqual(gray.size, (100, 100))

        # Invert
        inv = apply_filter(img, "invert")
        self.assertEqual(inv.size, (100, 100))
        # Inverted color of (120, 80, 200) should be (135, 175, 55)
        pixel = inv.getpixel((50, 50))
        self.assertEqual(pixel, (255 - 120, 255 - 80, 255 - 200))

    def test_watermark_suite(self):
        template_bytes = self._create_sample_image(600, 600)
        font_path = font_manager.get_font_path(script="english")

        # Create transparent PNG watermark
        wm_buf = io.BytesIO()
        wm_img = Image.new("RGBA", (100, 100), (255, 0, 0, 200))
        wm_img.save(wm_buf, format="PNG")
        wm_bytes = wm_buf.getvalue()

        # Test positions and scaling
        for pos in ["bottom_right", "bottom_left", "top_left", "top_right", "bottom_center"]:
            buf = _sync_render_worker(
                template_bytes=template_bytes,
                text="Watermark Test",
                font_path=font_path,
                variant=VARIANT_OVERLAY,
                watermark_bytes=wm_bytes,
                watermark_pos=pos,
                watermark_scale=1.5,
                watermark_opacity=0.5,
                watermark_enabled=True,
                is_clean=False,
            )
            self.assertIsInstance(buf, io.BytesIO)


if __name__ == "__main__":
    unittest.main()
