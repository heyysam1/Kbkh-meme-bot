"""Regression tests for the online search -> editor flow (2026-10-09).

Locks in:
- handle_external_use_template sets the SAME editor state fields as the
  normal catalog entry path (text_offset_y, font_scale, text_align,
  stroke_color, text_bg, flip, crop, brightness, contrast, banner_id,
  banner_position) so all 12 editor features work on online templates.
- The handler never dies silently: photo-upload and DB/state failures
  produce a visible error message.
- menu_search clears any active FSM state before prompting for a query.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from handlers import search_flow
from handlers.editor import EditorSG

REQUIRED_EDITOR_FIELDS = {
    "template_id": 42,
    "file_id": "FILEID123",
    "title": "Test Meme",
    "font_key": None,
    "variant": "overlay",
    "text_color": "white",
    "stroke_width": 4,
    "case_mode": "raw",
    "filter": "none",
    "watermark_enabled": True,
    "watermark_pos": "bottom_right",
    "watermark_scale": 1.0,
    "watermark_opacity": 0.8,
    "is_clean": False,
    "text_offset_y": 0,
    "font_scale": 1.0,
    "text_align": "center",
    "stroke_color": None,
    "text_bg": False,
    "flip": False,
    "crop": "off",
    "brightness": 1.0,
    "contrast": 1.0,
    "banner_id": None,
}


def _make_callback(data="btn_ext_use:ext_if_123", fail_photo=False):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = 999
    cb.answer = AsyncMock()
    msg = MagicMock()
    status_msg = AsyncMock()
    msg.answer = AsyncMock(return_value=status_msg)
    if fail_photo:
        msg.answer_photo = AsyncMock(side_effect=Exception("telegram down"))
    else:
        sent = MagicMock()
        size = MagicMock()
        size.file_id = "FILEID123"
        size.file_unique_id = "UNIQUE123"
        sent.photo = [MagicMock(), size]
        msg.answer_photo = AsyncMock(return_value=sent)
    cb.message = msg
    return cb


def _ext_item():
    return {"id": "ext_if_123", "title": "Test Meme", "name": "Test Meme",
            "url": "https://example.com/m.jpg", "source": "Imgflip"}


class TestExternalUseTemplate(unittest.IsolatedAsyncioTestCase):
    async def test_enters_editor_with_full_state(self):
        cb = _make_callback()
        state = MagicMock()
        state.clear = AsyncMock()
        state.update_data = AsyncMock()
        state.set_state = AsyncMock()
        with patch.object(search_flow, "get_external_template", return_value=_ext_item()), \
             patch.object(search_flow, "fetch_external_image_bytes", return_value=b"BYTES"), \
             patch.object(search_flow, "add_template", new=AsyncMock(return_value=42)), \
             patch.object(search_flow, "upsert_user",
                          new=AsyncMock(return_value={"preferred_font": "default",
                                                     "watermark_enabled": 1,
                                                     "watermark_position": "bottom_right",
                                                     "watermark_scale": 1.0,
                                                     "watermark_opacity": 0.8})), \
             patch.object(search_flow, "log_template_use", new=AsyncMock(), create=True), \
             patch.object(search_flow, "get_user_lang", new=AsyncMock(return_value="bn")):
            await search_flow.handle_external_use_template(cb, state)
        state.set_state.assert_awaited_once_with(EditorSG.waiting_for_text)
        data = state.update_data.await_args.kwargs
        for key, expected in REQUIRED_EDITOR_FIELDS.items():
            self.assertIn(key, data, f"missing editor state field: {key}")
            self.assertEqual(data[key], expected, f"wrong default for {key}")

    async def test_stale_registry_shows_alert(self):
        cb = _make_callback()
        state = MagicMock()
        with patch.object(search_flow, "get_external_template", return_value=None), \
             patch.object(search_flow, "get_user_lang", new=AsyncMock(return_value="bn")):
            await search_flow.handle_external_use_template(cb, state)
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))

    async def test_photo_upload_failure_is_visible(self):
        cb = _make_callback(fail_photo=True)
        state = MagicMock()
        state.set_state = AsyncMock()
        with patch.object(search_flow, "get_external_template", return_value=_ext_item()), \
             patch.object(search_flow, "fetch_external_image_bytes", return_value=b"BYTES"), \
             patch.object(search_flow, "get_user_lang", new=AsyncMock(return_value="bn")):
            await search_flow.handle_external_use_template(cb, state)
        # status message edited with error instead of silent death
        cb.message.answer.return_value.edit_text.assert_awaited_once()
        state.set_state.assert_not_awaited()

    async def test_db_failure_is_visible_not_silent(self):
        cb = _make_callback()
        state = MagicMock()
        state.clear = AsyncMock()
        with patch.object(search_flow, "get_external_template", return_value=_ext_item()), \
             patch.object(search_flow, "fetch_external_image_bytes", return_value=b"BYTES"), \
             patch.object(search_flow, "add_template",
                          new=AsyncMock(side_effect=Exception("db down"))), \
             patch.object(search_flow, "get_user_lang", new=AsyncMock(return_value="bn")):
            await search_flow.handle_external_use_template(cb, state)
        # error message sent after the photo instead of silence
        texts = [c.args[0] for c in cb.message.answer.await_args_list if c.args]
        self.assertTrue(any("ছবি আনা যায়নি" in t for t in texts))

    async def test_menu_search_clears_state(self):
        cb = MagicMock()
        cb.answer = AsyncMock()
        cb.message.answer = AsyncMock()
        cb.from_user.id = 999
        state = MagicMock()
        state.clear = AsyncMock()
        with patch.object(search_flow, "get_user_lang", new=AsyncMock(return_value="bn")):
            await search_flow.cb_menu_search(cb, state)
        state.clear.assert_awaited_once()
        cb.message.answer.assert_awaited_once()


class TestExternalDownload(unittest.IsolatedAsyncioTestCase):
    async def test_download_sends_photo_and_document(self):
        cb = _make_callback(data="btn_ext_dl:ext_if_123")
        cb.message.answer_photo = AsyncMock()
        cb.message.answer_document = AsyncMock()
        with patch.object(search_flow, "get_external_template", return_value=_ext_item()), \
             patch.object(search_flow, "fetch_external_image_bytes", return_value=b"BYTES"), \
             patch.object(search_flow, "get_user_lang", new=AsyncMock(return_value="bn")):
            await search_flow.handle_external_download_template(cb)
        cb.message.answer_photo.assert_awaited_once()
        cb.message.answer_document.assert_awaited_once()

    async def test_download_failure_is_visible(self):
        cb = _make_callback(data="btn_ext_dl:ext_if_123")
        cb.message.answer_photo = AsyncMock(side_effect=Exception("telegram down"))
        with patch.object(search_flow, "get_external_template", return_value=_ext_item()), \
             patch.object(search_flow, "fetch_external_image_bytes", return_value=b"BYTES"), \
             patch.object(search_flow, "get_user_lang", new=AsyncMock(return_value="bn")):
            await search_flow.handle_external_download_template(cb)
        cb.message.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
