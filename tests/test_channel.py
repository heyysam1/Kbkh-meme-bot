import unittest
from unittest.mock import MagicMock
from handlers.channel import extract_template_metadata


class TestChannelIngestion(unittest.TestCase):
    def test_extract_photo_with_caption(self):
        msg = MagicMock()
        msg.photo = [MagicMock(file_id="small"), MagicMock(file_id="large_123", file_unique_id="uniq_123")]
        msg.animation = None
        msg.video = None
        msg.document = None
        msg.caption = "Drake Hotline Bling\n#drake #hotline #meme"

        meta = extract_template_metadata(msg)
        self.assertIsNotNone(meta)
        file_id, file_unique_id, media_type, title, tags = meta
        self.assertEqual(file_id, "large_123")
        self.assertEqual(file_unique_id, "uniq_123")
        self.assertEqual(media_type, "photo")
        self.assertEqual(title, "Drake Hotline Bling")
        self.assertIn("drake", tags)
        self.assertIn("hotline", tags)

    def test_extract_video_without_caption_uses_filename(self):
        msg = MagicMock()
        msg.photo = None
        msg.animation = None
        msg.video = MagicMock(file_id="vid_999", file_unique_id="uniq_vid", file_name="funny_dancing_cat.mp4")
        msg.document = None
        msg.caption = None

        meta = extract_template_metadata(msg)
        self.assertIsNotNone(meta)
        file_id, file_unique_id, media_type, title, tags = meta
        self.assertEqual(file_id, "vid_999")
        self.assertEqual(media_type, "video")
        self.assertEqual(title, "funny dancing cat")

    def test_extract_photo_without_caption_fallback(self):
        msg = MagicMock()
        msg.photo = [MagicMock(file_id="ph_555", file_unique_id="uniq_555")]
        msg.animation = None
        msg.video = None
        msg.document = None
        msg.caption = None
        msg.chat.id = -100987654321
        msg.message_id = 42

        meta = extract_template_metadata(msg)
        self.assertIsNotNone(meta)
        file_id, file_unique_id, media_type, title, tags = meta
        self.assertEqual(file_id, "ph_555")
        self.assertEqual(media_type, "photo")
        self.assertEqual(title, "Template_100987654321_42")


if __name__ == "__main__":
    unittest.main()
