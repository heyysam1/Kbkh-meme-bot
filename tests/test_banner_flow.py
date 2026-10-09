"""Tests for the conversational /addbanner flow (2026-10-09).

Bare /addbanner must NOT go silent and must NOT send a "work in progress"
filler. It asks the admin to upload the banner photo ("upload your banner"
type prompt) and the next photo becomes the banner.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.admin import (
    BannerSG,
    handle_add_banner_command,
    handle_banner_photo_upload,
    handle_banner_upload_cancel,
)
from services.i18n import t


def _make_message(text=None, caption=None, photo=None, user_id=1):
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.photo = photo
    msg.reply_to_message = None
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _photo(file_id="FILE123"):
    p = MagicMock()
    p.file_id = file_id
    return [MagicMock(file_id="SMALL"), p]


class TestBannerUploadFlow(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_key_exists_both_languages(self):
        bn = t("admin.banner_upload_prompt", "bn")
        en = t("admin.banner_upload_prompt", "en")
        self.assertNotEqual(bn, "admin.banner_upload_prompt")
        self.assertNotEqual(en, "admin.banner_upload_prompt")
        # old usage key must be gone
        self.assertEqual(t("admin.addbanner_usage", "bn"), "admin.addbanner_usage")

    async def test_bare_command_asks_for_photo_and_sets_state(self):
        msg = _make_message(text="/addbanner")
        state = MagicMock()
        state.clear = AsyncMock()
        state.set_state = AsyncMock()
        with patch("handlers.admin.is_admin", return_value=True), patch(
            "handlers.admin.get_user_lang", new=AsyncMock(return_value="bn")
        ):
            await handle_add_banner_command(msg, state)
        state.set_state.assert_awaited_once_with(BannerSG.waiting_for_photo)
        msg.answer.assert_awaited_once()
        sent = msg.answer.await_args.args[0]
        self.assertIn("ছবি পাঠান", sent)

    async def test_photo_in_waiting_state_becomes_banner(self):
        msg = _make_message(photo=_photo(), caption="My Banner")
        state = MagicMock()
        state.clear = AsyncMock()
        with patch("handlers.admin.is_admin", return_value=True), patch(
            "handlers.admin.get_user_lang", new=AsyncMock(return_value="bn")
        ), patch("handlers.admin.add_banner", new=AsyncMock(return_value=7)) as mock_add:
            await handle_banner_photo_upload(msg, state)
        mock_add.assert_awaited_once()
        kwargs = mock_add.await_args.kwargs
        self.assertEqual(kwargs["name"], "My Banner")
        self.assertEqual(kwargs["file_id"], "FILE123")
        state.clear.assert_awaited_once()
        msg.answer.assert_awaited_once()

    async def test_photo_without_caption_uses_default_name(self):
        msg = _make_message(photo=_photo(), caption=None)
        state = MagicMock()
        state.clear = AsyncMock()
        with patch("handlers.admin.is_admin", return_value=True), patch(
            "handlers.admin.get_user_lang", new=AsyncMock(return_value="bn")
        ), patch("handlers.admin.add_banner", new=AsyncMock(return_value=8)) as mock_add:
            await handle_banner_photo_upload(msg, state)
        self.assertEqual(mock_add.await_args.kwargs["name"], "KBKH Promo Banner")

    async def test_cancel_clears_state(self):
        msg = _make_message(text="/cancel")
        state = MagicMock()
        state.clear = AsyncMock()
        with patch(
            "handlers.admin.get_user_lang", new=AsyncMock(return_value="bn")
        ):
            await handle_banner_upload_cancel(msg, state)
        state.clear.assert_awaited_once()
        msg.answer.assert_awaited_once()

    async def test_caption_flow_still_works_without_state(self):
        """Photo with /addbanner caption keeps working (quick path)."""
        msg = _make_message(caption="/addbanner Promo", photo=_photo())
        state = MagicMock()
        state.clear = AsyncMock()
        state.set_state = AsyncMock()
        with patch("handlers.admin.is_admin", return_value=True), patch(
            "handlers.admin.get_user_lang", new=AsyncMock(return_value="bn")
        ), patch("handlers.admin.add_banner", new=AsyncMock(return_value=9)) as mock_add:
            await handle_add_banner_command(msg, state)
        mock_add.assert_awaited_once()
        self.assertEqual(mock_add.await_args.kwargs["name"], "Promo")
        # state must NOT be set to waiting when a photo was already given
        state.set_state.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
