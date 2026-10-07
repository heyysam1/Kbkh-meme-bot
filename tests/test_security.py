import io
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
import config
from handlers.admin import is_admin
from services.font_manager import font_manager
from services.renderer import (
    _fit_text,
    _sync_render_worker,
)


class TestSecurityAuditFixes(unittest.TestCase):
    """Test suite validating remediation of all security audit findings."""

    def test_finding_1_admin_authorization_fail_closed(self):
        """Verify is_admin() fails closed when ADMIN_IDS is empty."""
        original_admin_ids = config.ADMIN_IDS
        try:
            # 1. When ADMIN_IDS is empty, no user is admin
            config.ADMIN_IDS = []
            self.assertFalse(is_admin(123456789))
            self.assertFalse(is_admin(1))

            # 2. When ADMIN_IDS is populated, only matching IDs are admin
            config.ADMIN_IDS = [999888777]
            self.assertTrue(is_admin(999888777))
            self.assertFalse(is_admin(123456789))
        finally:
            config.ADMIN_IDS = original_admin_ids

    def test_finding_2_channel_id_parsing(self):
        """Verify channel ID and admin ID parsing handles negative 64-bit Telegram IDs."""
        self.assertEqual(config._parse_channel_id("-1001987654321"), -1001987654321)
        self.assertIsNone(config._parse_channel_id(""))
        self.assertIsNone(config._parse_channel_id("invalid"))

        parsed_admins = config._parse_admin_ids("123, 456 -789 invalid 999")
        self.assertEqual(parsed_admins, [123, 456, -789, 999])

    def test_finding_3_fit_text_cpu_dos_guard(self):
        """Verify _fit_text handles massive inputs without excessive FreeType iteration loops."""
        font_path = font_manager.get_font_path(script="english")
        dummy_img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(dummy_img)

        # 300 words input designed to test iteration cap & truncation
        massive_text = "word " * 300
        wrapped, font, tw, th = _fit_text(
            massive_text,
            font_path=font_path,
            initial_size=96,
            max_width=400,
            max_height=300,
            draw=draw,
        )
        self.assertIsInstance(wrapped, str)
        self.assertGreater(len(wrapped), 0)
        self.assertLessEqual(tw, 400)

    def test_finding_4_pillow_max_image_pixels(self):
        """Verify Pillow MAX_IMAGE_PIXELS is capped defensively at 25 Megapixels."""
        self.assertEqual(Image.MAX_IMAGE_PIXELS, 25_000_000)

    def test_finding_4_large_template_downscaling(self):
        """Verify high-resolution images are downscaled to prevent memory exhaustion."""
        buf = io.BytesIO()
        # 3000 x 2000 image
        large_img = Image.new("RGB", (3000, 2000), color=(100, 150, 200))
        large_img.save(buf, format="JPEG")
        raw_bytes = buf.getvalue()

        font_path = font_manager.get_font_path(script="english")
        rendered_buf = _sync_render_worker(
            template_bytes=raw_bytes,
            text="Safety Test",
            font_path=font_path,
            variant="white_header",
            is_clean=True,
        )
        result_img = Image.open(rendered_buf)
        # Verify rendered width is downscaled to safe maximum canvas boundary
        self.assertLessEqual(result_img.width, 2560)


if __name__ == "__main__":
    unittest.main()
