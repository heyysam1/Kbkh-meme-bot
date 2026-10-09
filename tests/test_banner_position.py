"""Tests for the simplified promo-banner flow (bottom-only, add/remove/select)."""
import io
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from handlers.editor import (
    _banner_menu_kb,
    cb_banner_menu,
    cb_banner_remove,
)
from handlers.admin import handle_banner_upload_invalid
from services.renderer import _sync_render_worker


def _make_template_bytes(w=400, h=300, color=(50, 50, 50)):
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf.getvalue()


def _make_banner_bytes(w=400, h=80, color=(0, 120, 200)):
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def _make_callback(user_id=1):
    cb = MagicMock()
    cb.from_user.id = user_id
    cb.answer = AsyncMock()
    cb.bot = MagicMock()
    cb.message = MagicMock()
    return cb


class TestBannerRenderer(unittest.TestCase):
    def setUp(self):
        self.template = _make_template_bytes()
        self.banner = _make_banner_bytes()
        self.font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        if not self.font.exists():
            self.skipTest("DejaVu font not available")

    def _render(self):
        return _sync_render_worker(
            template_bytes=self.template,
            text="Test",
            font_path=self.font,
            variant="overlay",
            banner_bytes=self.banner,
        )

    def test_banner_extends_downward(self):
        """Promo banner is always appended at the bottom."""
        img = Image.open(self._render())
        self.assertEqual(img.size[0], 400)
        self.assertGreater(img.size[1], 300)
        r, g, b = img.getpixel((200, img.size[1] - 5))[:3]
        self.assertTrue(abs(r - 0) <= 8 and abs(g - 120) <= 8 and abs(b - 200) <= 8)

    def test_no_banner_unchanged(self):
        img = Image.open(_sync_render_worker(
            template_bytes=self.template, text="T", font_path=self.font,
        ))
        self.assertEqual(img.size, (400, 300))


class TestBannerMenu(unittest.IsolatedAsyncioTestCase):
    async def test_menu_kb_structure(self):
        """Menu has banner rows + remove + back, no position row."""
        banners = [{"id": 1, "name": "B1"}, {"id": 2, "name": None}]
        kb = _banner_menu_kb(banners, "bn")
        texts = [b.text for row in kb.inline_keyboard for b in row]
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn("edit:banner:apply:1", callbacks)
        self.assertIn("edit:banner:dl:2", callbacks)
        self.assertIn("edit:banner:remove", callbacks)
        self.assertIn("edit:back", callbacks)
        self.assertNotIn("edit:banner:pos", callbacks)
        for t in texts:
            self.assertNotIn("[", t)
            self.assertNotIn("]", t)

    async def test_empty_banners_admin_gets_guidance(self):
        cb = _make_callback(user_id=99)
        state = MagicMock()
        state.get_data = AsyncMock(return_value={})
        with patch("handlers.editor.get_all_banners", new=AsyncMock(return_value=[])), \
             patch("handlers.editor._lang_of", new=AsyncMock(return_value="bn")), \
             patch("handlers.editor.is_admin", return_value=True):
            await cb_banner_menu(cb, state)
        cb.answer.assert_awaited_once()
        msg = cb.answer.await_args.args[0]
        self.assertIn("/addbanner", msg)

    async def test_empty_banners_nonadmin_generic(self):
        cb = _make_callback(user_id=100)
        state = MagicMock()
        state.get_data = AsyncMock(return_value={})
        with patch("handlers.editor.get_all_banners", new=AsyncMock(return_value=[])), \
             patch("handlers.editor._lang_of", new=AsyncMock(return_value="bn")), \
             patch("handlers.editor.is_admin", return_value=False):
            await cb_banner_menu(cb, state)
        msg = cb.answer.await_args.args[0]
        self.assertNotIn("/addbanner", msg)

    async def test_remove_with_no_banner_warns(self):
        cb = _make_callback()
        state = MagicMock()
        state.get_data = AsyncMock(return_value={"banner_id": None})
        state.update_data = AsyncMock()
        with patch("handlers.editor._lang_of", new=AsyncMock(return_value="bn")):
            await cb_banner_remove(cb, state)
        cb.answer.assert_awaited_once()
        msg = cb.answer.await_args.args[0]
        self.assertIn("কোনো banner লাগানো নেই", msg)
        state.update_data.assert_not_awaited()

    async def test_waiting_state_invalid_input_guided(self):
        msg = MagicMock()
        msg.from_user.id = 1
        msg.answer = AsyncMock()
        with patch("handlers.admin.get_user_lang", new=AsyncMock(return_value="bn")):
            await handle_banner_upload_invalid(msg)
        msg.answer.assert_awaited_once()
        sent = msg.answer.await_args.args[0]
        self.assertIn("/cancel", sent)


if __name__ == "__main__":
    unittest.main()
