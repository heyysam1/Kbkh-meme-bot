import re
import unittest
from aiogram.fsm.state import State, StatesGroup
from handlers.submission import TemplateUploadSG
from handlers.editor import (
    EditorSG,
    LAYOUT_CYCLES,
    COLOR_CYCLES,
    STROKE_CYCLES,
    CASE_CYCLES,
    FILTER_CYCLES,
    WM_POS_CYCLES,
    WM_SCALE_CYCLES,
    get_editor_keyboard,
    get_layout_matrix_keyboard,
    get_font_matrix_keyboard,
    get_color_matrix_keyboard,
    get_stroke_matrix_keyboard,
    get_wm_pos_matrix_keyboard,
    get_wm_scale_matrix_keyboard,
    get_fx_matrix_keyboard,
)
from handlers.catalog import build_catalog_grid_keyboard
from handlers.start import get_main_menu_keyboard
from handlers.settings import get_settings_keyboard


class TestEditorAndSubmission(unittest.TestCase):
    def test_submission_fsm_states(self):
        """Verify TemplateUploadSG states structure."""
        self.assertTrue(issubclass(TemplateUploadSG, StatesGroup))
        self.assertIsInstance(TemplateUploadSG.waiting_for_media, State)
        self.assertIsInstance(TemplateUploadSG.waiting_for_title, State)

    def test_editor_fsm_states(self):
        """Verify EditorSG states structure."""
        self.assertTrue(issubclass(EditorSG, StatesGroup))
        self.assertIsInstance(EditorSG.waiting_for_text, State)
        self.assertIsInstance(EditorSG.editing, State)
        self.assertIsInstance(EditorSG.live_editing, State)

    def test_editor_cycles_completeness(self):
        """Verify layout, color, filter, stroke, case, and watermark cycles."""
        self.assertIn("overlay", LAYOUT_CYCLES)
        self.assertIn("top_banner", LAYOUT_CYCLES)
        self.assertIn("bottom_banner", LAYOUT_CYCLES)
        self.assertIn("breaking_news", LAYOUT_CYCLES)

        self.assertIn("deepfry", FILTER_CYCLES)
        self.assertIn("grayscale", FILTER_CYCLES)
        self.assertIn("invert", FILTER_CYCLES)
        self.assertIn("none", FILTER_CYCLES)

        self.assertIn("white", COLOR_CYCLES)
        self.assertIn("yellow", COLOR_CYCLES)

        self.assertIn("bottom_right", WM_POS_CYCLES)
        self.assertIn("bottom_center", WM_POS_CYCLES)

        self.assertIn(1.0, WM_SCALE_CYCLES)

    def test_zero_emoji_in_keyboards(self):
        """Ensure all generated inline keyboards strictly contain zero emojis."""
        emoji_pattern = re.compile(
            r'[\U00010000-\U0010ffff]|'
            r'[\u2600-\u27bf]|'
            r'[\u2300-\u23ff]|'
            r'[\u2b50-\u2b55]|'
            r'[\u3030\u303d]|'
            r'[\u2049\u203c]|'
            r'[\u00a9\u00ae]|'
            r'[\u2122\u2139]|'
            r'[\u25aa-\u25ab]|'
            r'[\u25b6-\u25c0]|'
            r'[\u25fb-\u25fe]'
        )

        sample_editor_data = {
            "variant": "overlay",
            "text_color": "yellow",
            "stroke_width": 4,
            "case_mode": "upper",
            "filter": "deepfry",
            "watermark_enabled": True,
            "watermark_pos": "bottom_right",
            "is_clean": False,
        }
        editor_kb = get_editor_keyboard(sample_editor_data)
        for row in editor_kb.inline_keyboard:
            for btn in row:
                self.assertFalse(
                    bool(emoji_pattern.search(btn.text)),
                    f"Emoji detected in editor button: {btn.text}",
                )

        grid_kb = build_catalog_grid_keyboard(
            templates=[{"id": 1, "title": "Meme A"}, {"id": 2, "title": "Meme B"}],
            page=1,
            total_pages=5,
        )
        for row in grid_kb.inline_keyboard:
            for btn in row:
                self.assertFalse(
                    bool(emoji_pattern.search(btn.text)),
                    f"Emoji detected in grid button: {btn.text}",
                )

        start_kb = get_main_menu_keyboard()
        for row in start_kb.inline_keyboard:
            for btn in row:
                self.assertFalse(
                    bool(emoji_pattern.search(btn.text)),
                    f"Emoji detected in start button: {btn.text}",
                )

        user_mock = {
            "watermark_enabled": 1,
            "watermark_position": "bottom_right",
            "watermark_scale": 1.0,
            "watermark_opacity": 0.8,
        }
        settings_kb = get_settings_keyboard(user_mock)
        for row in settings_kb.inline_keyboard:
            for btn in row:
                self.assertFalse(
                    bool(emoji_pattern.search(btn.text)),
                    f"Emoji detected in settings button: {btn.text}",
                )

        matrix_keyboards = [
            get_layout_matrix_keyboard(),
            get_font_matrix_keyboard(),
            get_color_matrix_keyboard(),
            get_stroke_matrix_keyboard(),
            get_wm_pos_matrix_keyboard(),
            get_wm_scale_matrix_keyboard(),
            get_fx_matrix_keyboard(),
        ]
        for kb in matrix_keyboards:
            for row in kb.inline_keyboard:
                for btn in row:
                    self.assertFalse(
                        bool(emoji_pattern.search(btn.text)),
                        f"Emoji detected in matrix button: {btn.text}",
                    )

    def test_editor_callback_matrix_wiring(self):
        """Verify matrix keyboard callbacks follow edit:* specification."""
        layout_kb = get_layout_matrix_keyboard()
        layout_cbs = [btn.callback_data for row in layout_kb.inline_keyboard for btn in row]
        self.assertIn("edit:layout:overlay", layout_cbs)
        self.assertIn("edit:layout:top_banner", layout_cbs)
        self.assertIn("edit:layout:bottom_banner", layout_cbs)
        self.assertIn("edit:layout:breaking", layout_cbs)

        font_kb = get_font_matrix_keyboard()
        font_cbs = [btn.callback_data for row in font_kb.inline_keyboard for btn in row]
        self.assertIn("edit:font:kalpurush", font_cbs)
        self.assertIn("edit:font:anek_bangla", font_cbs)
        self.assertIn("edit:font:impact", font_cbs)

        color_kb = get_color_matrix_keyboard()
        color_cbs = [btn.callback_data for row in color_kb.inline_keyboard for btn in row]
        self.assertIn("edit:color:#FFFFFF", color_cbs)
        self.assertIn("edit:color:#000000", color_cbs)
        self.assertIn("edit:color:#FFE600", color_cbs)

        stroke_kb = get_stroke_matrix_keyboard()
        stroke_cbs = [btn.callback_data for row in stroke_kb.inline_keyboard for btn in row]
        self.assertIn("edit:stroke:0", stroke_cbs)
        self.assertIn("edit:stroke:2", stroke_cbs)
        self.assertIn("edit:stroke:5", stroke_cbs)
        self.assertIn("edit:stroke:8", stroke_cbs)

        wm_pos_kb = get_wm_pos_matrix_keyboard()
        wm_pos_cbs = [btn.callback_data for row in wm_pos_kb.inline_keyboard for btn in row]
        self.assertIn("edit:wm_pos:tl", wm_pos_cbs)
        self.assertIn("edit:wm_pos:tr", wm_pos_cbs)
        self.assertIn("edit:wm_pos:bl", wm_pos_cbs)
        self.assertIn("edit:wm_pos:br", wm_pos_cbs)
        self.assertIn("edit:wm_pos:bc", wm_pos_cbs)

        wm_scale_kb = get_wm_scale_matrix_keyboard()
        wm_scale_cbs = [btn.callback_data for row in wm_scale_kb.inline_keyboard for btn in row]
        self.assertIn("edit:wm_scale:0.5", wm_scale_cbs)
        self.assertIn("edit:wm_scale:1.0", wm_scale_cbs)
        self.assertIn("edit:wm_scale:1.5", wm_scale_cbs)
        self.assertIn("edit:wm_scale:2.0", wm_scale_cbs)

        fx_kb = get_fx_matrix_keyboard()
        fx_cbs = [btn.callback_data for row in fx_kb.inline_keyboard for btn in row]
        self.assertIn("edit:fx:none", fx_cbs)
        self.assertIn("edit:fx:deepfry", fx_cbs)
        self.assertIn("edit:fx:grayscale", fx_cbs)
        self.assertIn("edit:fx:invert", fx_cbs)


if __name__ == "__main__":
    unittest.main()
