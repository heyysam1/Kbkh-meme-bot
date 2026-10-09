"""End-to-end mock tests for handlers/settings.py (2026-10-09).

Exercises EVERY settings sub-option with a real temp SQLite DB:
- /settings command + menu_settings button -> dashboard render
- cb_toggle_lang (language flip)
- cb_toggle_wm (watermark on/off)
- cb_upload_wm + waiting_for_watermark + handle_receive_watermark
- cb_pos_wm_menu + cb_set_pos:
- cb_scale_wm_menu + cb_set_scale:
- cb_opacity_wm_menu + cb_set_opac:
- cb_text_wm_prompt + waiting_for_wm_text + handle_receive_wm_text
- cb_pref_font_menu + cb_set_pref_font:
- cb_cancel_settings
- menu_home (must match current main dashboard, no brackets)
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

import config
from database.db import init_db
from database.queries import get_user, get_user_lang

from handlers import settings as settings_mod
from handlers.settings import (
    SettingsSG,
    get_settings_keyboard,
    handle_settings_command,
    handle_toggle_lang,
    handle_toggle_watermark,
    handle_upload_wm_prompt,
    handle_receive_watermark,
    handle_text_wm_prompt,
    handle_receive_wm_text,
    handle_cancel_settings,
    handle_wm_position_menu,
    handle_set_wm_position,
    handle_wm_scale_menu,
    handle_set_scale,
    handle_wm_opacity_menu,
    handle_set_opacity,
    handle_pref_font_menu,
    handle_set_pref_font,
    cb_menu_home,
)


def _mock_callback(data="menu_settings", user_id=999001):
    from aiogram import types as aiogram_types
    cb = MagicMock()
    cb.__class__ = aiogram_types.CallbackQuery
    cb.data = data
    cb.from_user.id = user_id
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.__class__ = aiogram_types.Message
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    return cb


def _mock_message(text="/settings", user_id=999001):
    from aiogram import types as aiogram_types
    m = MagicMock()
    m.__class__ = aiogram_types.Message
    m.text = text
    m.caption = None
    m.photo = None
    m.document = None
    m.reply_to_message = None
    m.from_user.id = user_id
    m.from_user.full_name = "Test User"
    m.answer = AsyncMock()
    return m


def _mock_state():
    s = MagicMock()
    s.clear = AsyncMock()
    s.set_state = AsyncMock()
    s.update_data = AsyncMock()
    s.get_state = AsyncMock(return_value=None)
    return s


class TestSettingsFlows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._orig = config.DB_PATH
        config.DB_PATH = Path(self.temp_dir.name) / "test_settings.db"
        await init_db()
        self.uid = 999001

    async def asyncTearDown(self):
        config.DB_PATH = self._orig
        self.temp_dir.cleanup()

    async def test_settings_command_renders_dashboard(self):
        msg = _mock_message()
        await handle_settings_command(msg, _mock_state())
        msg.answer.assert_awaited_once()
        text = msg.answer.await_args.args[0]
        self.assertIn("সেটিংস", text)  # bn default title
        kb = msg.answer.await_args.kwargs["reply_markup"]
        self.assertIsNotNone(kb)

    async def test_menu_settings_button_renders_dashboard(self):
        cb = _mock_callback("menu_settings")
        await handle_settings_command(cb, _mock_state())
        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.await_args.args[0]
        # no bracket wrapping anywhere
        self.assertNotIn("[", text.split("\n")[0])

    async def test_toggle_lang_flips_bn_en(self):
        self.assertEqual(await get_user_lang(self.uid), "bn")
        cb = _mock_callback("cb_toggle_lang")
        await handle_toggle_lang(cb)
        self.assertEqual(await get_user_lang(self.uid), "en")
        # dashboard re-rendered in English
        text = cb.message.edit_text.await_args.args[0]
        self.assertIn("Settings", text)
        # flip back
        cb2 = _mock_callback("cb_toggle_lang")
        await handle_toggle_lang(cb2)
        self.assertEqual(await get_user_lang(self.uid), "bn")

    async def test_toggle_watermark(self):
        cb = _mock_callback("cb_toggle_wm")
        await handle_toggle_watermark(cb)
        user = await get_user(self.uid)
        self.assertEqual(user["watermark_enabled"], 1)
        cb2 = _mock_callback("cb_toggle_wm")
        await handle_toggle_watermark(cb2)
        user = await get_user(self.uid)
        self.assertEqual(user["watermark_enabled"], 0)

    async def test_watermark_image_upload_flow(self):
        cb = _mock_callback("cb_upload_wm")
        state = _mock_state()
        await handle_upload_wm_prompt(cb, state)
        state.set_state.assert_awaited_once_with(SettingsSG.waiting_for_watermark)

        # now send a photo
        msg = _mock_message()
        photo = MagicMock()
        photo.file_id = "WMFILE123"
        msg.photo = [MagicMock(file_id="SMALL"), photo]
        state2 = _mock_state()
        await handle_receive_watermark(msg, state2)
        state2.clear.assert_awaited_once()
        user = await get_user(self.uid)
        self.assertEqual(user["watermark_file_id"], "WMFILE123")
        self.assertEqual(user["watermark_enabled"], 1)
        msg.answer.assert_awaited_once()

    async def test_watermark_upload_rejects_non_image_document(self):
        msg = _mock_message()
        doc = MagicMock()
        doc.mime_type = "application/pdf"
        doc.file_size = 1000
        msg.document = doc
        state = _mock_state()
        await handle_receive_watermark(msg, state)
        # state NOT cleared, error sent
        state.clear.assert_not_awaited()
        msg.answer.assert_awaited_once()

    async def test_position_menu_and_set(self):
        cb = _mock_callback("cb_pos_wm_menu")
        await handle_wm_position_menu(cb)
        cb.message.edit_text.assert_awaited_once()
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        # 5 position buttons + back
        total_btns = sum(len(r) for r in kb.inline_keyboard)
        self.assertEqual(total_btns, 6)

        cb2 = _mock_callback("cb_set_pos:top_left")
        await handle_set_wm_position(cb2)
        user = await get_user(self.uid)
        self.assertEqual(user["watermark_position"], "top_left")

    async def test_scale_menu_and_set(self):
        cb = _mock_callback("cb_scale_wm_menu")
        await handle_wm_scale_menu(cb)
        cb.message.edit_text.assert_awaited_once()

        cb2 = _mock_callback("cb_set_scale:1.5")
        await handle_set_scale(cb2)
        user = await get_user(self.uid)
        self.assertAlmostEqual(user["watermark_scale"], 1.5)

    async def test_opacity_menu_and_set(self):
        cb = _mock_callback("cb_opacity_wm_menu")
        await handle_wm_opacity_menu(cb)
        cb.message.edit_text.assert_awaited_once()

        cb2 = _mock_callback("cb_set_opac:0.25")
        await handle_set_opacity(cb2)
        user = await get_user(self.uid)
        self.assertAlmostEqual(user["watermark_opacity"], 0.25)

    async def test_text_watermark_flow(self):
        cb = _mock_callback("cb_text_wm_prompt")
        state = _mock_state()
        await handle_text_wm_prompt(cb, state)
        state.set_state.assert_awaited_once_with(SettingsSG.waiting_for_wm_text)

        msg = _mock_message(text="MyBrand")
        state2 = _mock_state()
        await handle_receive_wm_text(msg, state2)
        state2.clear.assert_awaited_once()
        user = await get_user(self.uid)
        self.assertEqual(user["watermark_text"], "MyBrand")

        # /clear clears it
        msg2 = _mock_message(text="/clear")
        await handle_receive_wm_text(msg2, _mock_state())
        user = await get_user(self.uid)
        self.assertEqual(user["watermark_text"], "")

    async def test_cancel_settings(self):
        cb = _mock_callback("cb_cancel_settings")
        state = _mock_state()
        await handle_cancel_settings(cb, state)
        state.clear.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()  # back to dashboard

    async def test_pref_font_menu_and_set(self):
        cb = _mock_callback("cb_pref_font_menu")
        await handle_pref_font_menu(cb)
        cb.message.edit_text.assert_awaited_once()
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        # at least system default + back + some fonts
        total_btns = sum(len(r) for r in kb.inline_keyboard)
        self.assertGreater(total_btns, 3)

        cb2 = _mock_callback("cb_set_pref_font:default")
        await handle_set_pref_font(cb2)
        user = await get_user(self.uid)
        self.assertEqual(user["preferred_font"], "default")

    async def test_menu_home_matches_dashboard(self):
        cb = _mock_callback("menu_home")
        await cb_menu_home(cb)
        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.await_args.args[0]
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        # must NOT contain old bracket text
        self.assertNotIn("[KBKH MEME ENGINE", text)
        self.assertNotIn("[", text.split("\n")[0])
        # keyboard must be the main menu (has menu_settings button)
        all_cb = [b.callback_data for r in kb.inline_keyboard for b in r]
        self.assertIn("menu_settings", all_cb)

    async def test_wrong_input_in_watermark_state_gets_guidance(self):
        """Dead end fix: text sent while waiting for watermark photo -> hint, not silence."""
        from handlers.settings import handle_watermark_wrong_input
        msg = _mock_message(text="hello")
        await handle_watermark_wrong_input(msg)
        msg.answer.assert_awaited_once()
        sent = msg.answer.await_args.args[0]
        self.assertIn("ছবি পাঠান", sent)

    async def test_wrong_input_in_wm_text_state_gets_guidance(self):
        """Dead end fix: photo sent while waiting for watermark text -> hint, not silence."""
        from handlers.settings import handle_wm_text_wrong_input
        msg = _mock_message()
        msg.photo = [MagicMock(file_id="X")]
        await handle_wm_text_wrong_input(msg)
        msg.answer.assert_awaited_once()
        sent = msg.answer.await_args.args[0]
        self.assertIn("/clear", sent)

    async def test_get_settings_keyboard_buttons(self):
        from database.queries import upsert_user
        user = await upsert_user(self.uid)
        for lang in ("bn", "en"):
            kb = get_settings_keyboard(user, lang)
            all_cb = [b.callback_data for r in kb.inline_keyboard for b in r]
            for expected in ("cb_toggle_lang", "cb_toggle_wm", "cb_pos_wm_menu",
                             "cb_scale_wm_menu", "cb_opacity_wm_menu",
                             "cb_text_wm_prompt", "cb_upload_wm",
                             "cb_pref_font_menu", "menu_home"):
                self.assertIn(expected, all_cb, f"{expected} missing for lang={lang}")
            # no brackets or emojis in button texts
            for b in [btn for r in kb.inline_keyboard for btn in r]:
                self.assertFalse(b.text.startswith("["), f"bracket: {b.text}")


if __name__ == "__main__":
    unittest.main()
