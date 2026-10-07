import unittest
from pathlib import Path
import config
from services.font_manager import FontManager, font_manager


class TestFontManager(unittest.TestCase):
    def test_font_scanning(self):
        fm = FontManager()
        self.assertGreaterEqual(len(fm._fonts), 15)
        # Verify primary defaults exist
        self.assertIn(config.DEFAULT_BENGALI_FONT, fm._fonts)
        self.assertIn(config.DEFAULT_ENGLISH_FONT, fm._fonts)

    def test_script_detection(self):
        # Bengali detection
        self.assertEqual(font_manager.detect_script("বাংলা মিম"), "bengali")
        self.assertEqual(font_manager.detect_script("Hello বাংলা"), "bengali")
        # English detection
        self.assertEqual(font_manager.detect_script("Hello world"), "english")
        self.assertEqual(font_manager.detect_script("12345!@#$"), "english")

    def test_font_resolution_and_fallback(self):
        # Specific valid key
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
        fm = FontManager()
        cleaned = fm.sanitize_display_name("HindSiliguri-Bold.ttf")
        self.assertEqual(cleaned, "Hind Siliguri (Bold)")

        cleaned_impact = fm.sanitize_display_name("Impact.ttf")
        self.assertEqual(cleaned_impact, "Impact (Classic Meme)")


if __name__ == "__main__":
    unittest.main()
