import unittest
from pathlib import Path
import config
from services.font_manager import FontManager, font_manager, CURATED_FONTS


class TestFontManager(unittest.TestCase):
    def test_exactly_ten_fonts_exist_in_directory(self):
        """Assert that assets/fonts directory strictly contains exactly the 9 allowed fonts."""
        fonts = [
            f for f in config.FONTS_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in (".ttf", ".otf")
        ]
        self.assertEqual(
            len(fonts),
            9,
            f"Expected exactly 9 fonts in assets/fonts, found {len(fonts)}: {[f.name for f in fonts]}",
        )

    def test_exactly_ten_curated_fonts_registered(self):
        """Verify FontManager registers strictly the 9 approved fonts."""
        fm = FontManager()
        font_list = fm.get_font_list()
        self.assertEqual(len(font_list), 9, f"Expected exactly 9 registered fonts, got {len(font_list)}")

        registered_displays = [name for _, name in font_list]
        expected_displays = [
            "Anek Bangla",
            "Anek ExtraBold",
            "Li Siliguri",
            "Headline Bangla",
            "Noto Sans Bengali",
            "Impact",
            "Anton",
            "Inter",
            "Poppins Bold",
        ]
        self.assertEqual(registered_displays, expected_displays)

    def test_script_detection(self):
        """Verify Bengali vs English Unicode block detection."""
        self.assertEqual(font_manager.detect_script("বাংলা মিম"), "bengali")
        self.assertEqual(font_manager.detect_script("Hello বাংলা"), "bengali")
        self.assertEqual(font_manager.detect_script("Hello world"), "english")
        self.assertEqual(font_manager.detect_script("12345!@#$"), "english")

    def test_font_resolution_and_fallback(self):
        """Verify key resolution and script-aware fallbacks."""
        # Valid key
        path = font_manager.get_font_path("Impact.ttf")
        self.assertEqual(path.name, "Impact.ttf")
        self.assertTrue(path.exists())

        # Non-existent key falls back to Bengali default
        fallback_bengali = font_manager.get_font_path("NonExistentFont.ttf", script="bengali")
        self.assertEqual(fallback_bengali.name, config.DEFAULT_BENGALI_FONT)

        # Non-existent key falls back to English default
        fallback_english = font_manager.get_font_path("NonExistentFont.ttf", script="english")
        self.assertEqual(fallback_english.name, config.DEFAULT_ENGLISH_FONT)

    def test_display_name_sanitization(self):
        """Verify clean emoji-free display names for the 9 approved fonts."""
        fm = FontManager()
        self.assertEqual(fm.sanitize_display_name("AnekBangla.ttf"), "Anek Bangla")
        self.assertEqual(fm.sanitize_display_name("AnekBangla-ExtraBold.ttf"), "Anek ExtraBold")
        self.assertEqual(fm.sanitize_display_name("LiSiliguri.ttf"), "Li Siliguri")
        self.assertEqual(fm.sanitize_display_name("HeadlineBangla.ttf"), "Headline Bangla")
        self.assertEqual(fm.sanitize_display_name("NotoSansBengali.ttf"), "Noto Sans Bengali")
        self.assertEqual(fm.sanitize_display_name("Impact.ttf"), "Impact")
        self.assertEqual(fm.sanitize_display_name("Anton-Regular.ttf"), "Anton")
        self.assertEqual(fm.sanitize_display_name("Inter-Bold.ttf"), "Inter")
        self.assertEqual(fm.sanitize_display_name("Poppins-Bold.ttf"), "Poppins Bold")

    def test_load_font_with_raqm_layout(self):
        """Verify font loading returns valid FreeTypeFont."""
        path = font_manager.get_font_path(config.DEFAULT_BENGALI_FONT)
        font = font_manager.load_font(path, 24)
        self.assertIsNotNone(font)


if __name__ == "__main__":
    unittest.main()
