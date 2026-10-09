"""Tests for the 12 new editor fine-tune features (nudge, font size, align,
stroke color, text bg, flip, crop, brightness/contrast, undo, presets,
recent templates, shuffle) plus renderer support for the new parameters."""

import asyncio
import io
import tempfile
import unittest
from pathlib import Path

import config
from database.db import init_db
from database.queries import log_template_use, get_recent_templates
from handlers.editor import (
    get_editor_keyboard,
    get_preset_keyboard,
    make_snapshot,
    push_snapshot,
    pop_snapshot,
    apply_preset,
    shuffled_style,
    STYLE_PRESETS,
    FONT_MAP,
    COLOR_CYCLES,
    STROKE_CYCLES,
    FILTER_CYCLES,
    _DRAFT_KEYS,
)


def _all_callback_data(kb):
    return [btn.callback_data for row in kb.inline_keyboard for btn in row]


class TestEditorKeyboardFeatures(unittest.TestCase):
    def test_keyboard_contains_all_new_feature_buttons(self):
        kb = get_editor_keyboard({}, lang="bn")
        cbs = _all_callback_data(kb)
        for expected in [
            "edit:nudge:up", "edit:nudge:down",
            "edit:fsize:up", "edit:fsize:down",
            "edit:align:cycle",
            "edit:strokecolor:cycle",
            "edit:textbg:toggle",
            "edit:flip:toggle",
            "edit:crop:cycle",
            "edit:bright:up", "edit:bright:down",
            "edit:contrast:up", "edit:contrast:down",
            "edit:undo",
            "edit:menu:presets",
            "edit:shuffle",
        ]:
            self.assertIn(expected, cbs, f"missing button {expected}")

    def test_keyboard_has_no_bracket_wrapping(self):
        for lang in ("bn", "en"):
            kb = get_editor_keyboard({}, lang=lang)
            for row in kb.inline_keyboard:
                for btn in row:
                    self.assertNotIn("[", btn.text, f"bracket in {lang} button: {btn.text}")
                    self.assertNotIn("]", btn.text, f"bracket in {lang} button: {btn.text}")

    def test_keyboard_reflects_state(self):
        data = {"text_align": "left", "crop": "square", "text_bg": True, "flip": True,
                "stroke_color": "red"}
        kb = get_editor_keyboard(data, lang="en")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        joined = " ".join(texts)
        self.assertIn("Left", joined)
        self.assertIn("Square", joined)
        self.assertIn("Red", joined)

    def test_preset_keyboard_buttons(self):
        kb = get_preset_keyboard(lang="en")
        cbs = _all_callback_data(kb)
        self.assertIn("edit:preset:classic", cbs)
        self.assertIn("edit:preset:modern", cbs)
        self.assertIn("edit:preset:bold", cbs)


class TestUndoLogic(unittest.TestCase):
    def test_snapshot_excludes_volatile_keys(self):
        data = {"text": "hi", "variant": "overlay", "history": [1],
                "preview_message_id": 5, "template_bytes": b"xx"}
        snap = make_snapshot(data)
        self.assertEqual(snap, {"text": "hi", "variant": "overlay"})

    def test_push_snapshot_keeps_last_10(self):
        hist = []
        for i in range(15):
            hist = push_snapshot(hist, {"n": i})
        self.assertEqual(len(hist), 10)
        self.assertEqual(hist[0], {"n": 5})
        self.assertEqual(hist[-1], {"n": 14})

    def test_pop_snapshot_roundtrip(self):
        hist = push_snapshot([], {"a": 1})
        hist = push_snapshot(hist, {"a": 2})
        prev, rest = pop_snapshot(hist)
        self.assertEqual(prev, {"a": 2})
        self.assertEqual(len(rest), 1)
        prev2, rest2 = pop_snapshot(rest)
        self.assertEqual(prev2, {"a": 1})
        self.assertEqual(rest2, [])
        prev3, rest3 = pop_snapshot(rest2)
        self.assertIsNone(prev3)
        self.assertEqual(rest3, [])

    def test_draft_keys_include_new_render_keys_but_not_history(self):
        for key in ["text_offset_y", "font_scale", "text_align", "stroke_color",
                    "text_bg", "flip", "crop", "brightness", "contrast"]:
            self.assertIn(key, _DRAFT_KEYS)
        self.assertNotIn("history", _DRAFT_KEYS)
        self.assertNotIn("preview_message_id", _DRAFT_KEYS)


class TestPresetsAndShuffle(unittest.TestCase):
    def test_apply_preset_sets_all_keys(self):
        data = {"text": "hi", "variant": "overlay"}
        for name, preset in STYLE_PRESETS.items():
            out = apply_preset(data, name)
            for k, v in preset.items():
                self.assertEqual(out[k], v, f"preset {name} key {k}")
            self.assertEqual(out["text"], "hi")  # untouched keys survive

    def test_apply_preset_unknown_name_is_noop(self):
        data = {"text": "hi"}
        self.assertEqual(apply_preset(data, "nope"), data)

    def test_shuffled_style_uses_valid_values(self):
        for _ in range(20):
            combo = shuffled_style()
            self.assertIn(combo["font_key"], set(FONT_MAP.values()))
            self.assertIn(combo["text_color"], COLOR_CYCLES)
            self.assertIn(combo["stroke_width"], STROKE_CYCLES)
            self.assertIn(combo["filter"], FILTER_CYCLES)


class TestRendererNewParams(unittest.TestCase):
    def _make_image(self, w=600, h=400):
        from PIL import Image
        img = Image.new("RGB", (w, h), (30, 120, 200))
        # asymmetric marker: red square on the left half (for flip test)
        px = img.load()
        for x in range(0, 60):
            for y in range(0, 60):
                px[x, y] = (255, 0, 0)
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_render_with_all_new_params(self):
        from services.renderer import render_meme
        fp = Path("assets/fonts/NotoSansBengali.ttf")
        out = asyncio.run(render_meme(
            self._make_image(), "hello | world", fp,
            text_offset_y=30, font_scale=1.3, text_align="left",
            stroke_color="red", text_bg=True, flip=True, crop="square",
            brightness=1.2, contrast=1.1,
        ))
        data = out.getvalue()
        self.assertTrue(len(data) > 1000)
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        self.assertEqual(im.size, (400, 400))  # square crop of 600x400

    def test_crop_45_aspect(self):
        from services.renderer import render_meme
        fp = Path("assets/fonts/NotoSansBengali.ttf")
        out = asyncio.run(render_meme(
            self._make_image(600, 400), "hi", fp, crop="4:5", is_clean=True,
        ))
        from PIL import Image
        im = Image.open(io.BytesIO(out.getvalue()))
        w, h = im.size
        self.assertAlmostEqual(w / h, 0.8, places=1)

    def test_flip_mirrors_image(self):
        from services.renderer import _sync_render_worker
        fp = Path("assets/fonts/NotoSansBengali.ttf")
        base = _sync_render_worker(self._make_image(), "", fp, is_clean=True,
                                   crop="off").getvalue()
        flipped = _sync_render_worker(self._make_image(), "", fp, is_clean=True,
                                      flip=True, crop="off").getvalue()
        from PIL import Image
        a = Image.open(io.BytesIO(base))
        b = Image.open(io.BytesIO(flipped))
        # red marker was top-left; after mirror it must be top-right
        # (JPEG noise: check red dominance instead of exact 255)
        self.assertNotEqual(a.tobytes(), b.tobytes())
        self.assertGreater(b.getpixel((b.width - 30, 30))[0], 200)


class TestRecentTemplates(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._orig = config.DB_PATH
        config.DB_PATH = Path(self.temp_dir.name) / "test_recent.db"
        await init_db()

    async def asyncTearDown(self):
        config.DB_PATH = self._orig
        self.temp_dir.cleanup()

    async def test_log_and_get_recent_templates(self):
        self.assertEqual(await get_recent_templates(42), [])
        await log_template_use(42, 7)
        await log_template_use(42, 9)
        await log_template_use(42, 7)  # repeat use bumps recency
        recent = await get_recent_templates(42)
        self.assertEqual(recent[0], 7)
        self.assertEqual(set(recent), {7, 9})
        limited = await get_recent_templates(42, limit=1)
        self.assertEqual(limited, [7])
        # other users are isolated
        self.assertEqual(await get_recent_templates(43), [])


if __name__ == "__main__":
    unittest.main()
