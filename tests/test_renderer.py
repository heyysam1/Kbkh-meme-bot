import io
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
import config
from services.font_manager import font_manager
from services.renderer import (
    _calculate_average_luminance,
    _wrap_text_to_width,
    _fit_text,
    _sync_render_worker,
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

    def test_render_variants(self):
        template_bytes = self._create_sample_image(500, 400)
        font_path = font_manager.get_font_path(script="bengali")

        # Variant A: White Header
        buf_a = _sync_render_worker(
            template_bytes=template_bytes,
            text="টেস্টিং বাংলা মিম টেক্সট",
            font_path=font_path,
            variant="white_header",
            is_clean=True,
        )
        self.assertIsInstance(buf_a, io.BytesIO)
        img_a = Image.open(buf_a)
        self.assertEqual(img_a.size[0], 500)
        self.assertGreater(img_a.size[1], 400)

        # Variant B: Dark Header
        buf_b = _sync_render_worker(
            template_bytes=template_bytes,
            text="Dark Header Variant Test",
            font_path=font_path,
            variant="dark_header",
            is_clean=True,
        )
        img_b = Image.open(buf_b)
        self.assertEqual(img_b.size[0], 500)
        self.assertGreater(img_b.size[1], 400)

        # Variant C: Classic Overlay (split with |)
        buf_c = _sync_render_worker(
            template_bytes=template_bytes,
            text="TOP TEXT | BOTTOM TEXT",
            font_path=font_path,
            variant="classic_overlay",
            is_clean=True,
        )
        img_c = Image.open(buf_c)
        self.assertEqual(img_c.size[0], 500)
        self.assertEqual(img_c.size[1], 400)

    def test_banner_extension(self):
        template_bytes = self._create_sample_image(400, 300)
        banner_bytes = self._create_sample_image(800, 150)
        font_path = font_manager.get_font_path(script="english")

        buf_with_banner = _sync_render_worker(
            template_bytes=template_bytes,
            text="With Banner Test",
            font_path=font_path,
            variant="white_header",
            banner_bytes=banner_bytes,
        )
        img_with_banner = Image.open(buf_with_banner)

        buf_no_banner = _sync_render_worker(
            template_bytes=template_bytes,
            text="With Banner Test",
            font_path=font_path,
            variant="white_header",
            banner_bytes=None,
        )
        img_no_banner = Image.open(buf_no_banner)

        self.assertGreater(img_with_banner.size[1], img_no_banner.size[1])


if __name__ == "__main__":
    unittest.main()
