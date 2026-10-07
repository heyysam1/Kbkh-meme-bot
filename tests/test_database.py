import os
import tempfile
import unittest
from pathlib import Path
import config
from database.db import get_db, init_db
from database.queries import (
    add_template,
    get_template_by_id,
    get_random_template,
    get_templates_paginated,
    get_templates_count,
    increment_template_usage,
    update_user_font,
    get_user,
    add_alias,
    resolve_alias,
    add_source,
    get_all_sources,
    remove_source,
    update_user_watermark_settings,
)


class TestDatabase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = Path(self.temp_dir.name) / "test_bot.db"
        self._orig_db_path = config.DB_PATH
        config.DB_PATH = self.test_db_path

    async def asyncTearDown(self):
        config.DB_PATH = self._orig_db_path
        self.temp_dir.cleanup()

    async def test_database_lifecycle(self):
        # 1. Initialize schema & auto-migrations
        await init_db()
        self.assertTrue(self.test_db_path.exists())

        # Verify columns exist via PRAGMA
        async with get_db() as conn:
            async with conn.execute("PRAGMA table_info(templates);") as cursor:
                cols = {row[1] for row in await cursor.fetchall()}
                self.assertIn("media_type", cols)
                self.assertIn("file_unique_id", cols)
                self.assertIn("title", cols)
                self.assertIn("source_channel_id", cols)
                self.assertIn("added_by", cols)

            async with conn.execute("PRAGMA table_info(users);") as cursor:
                user_cols = {row[1] for row in await cursor.fetchall()}
                self.assertIn("watermark_scale", user_cols)
                self.assertIn("watermark_opacity", user_cols)
                self.assertIn("watermark_text", user_cols)

        # 2. Template CRUD with multi-mime & metadata & added_by
        tid = await add_template(
            file_id="tg_file_id_123",
            name="Leonardo DiCaprio Laughing",
            tags="django leonardo drink laugh",
            is_trending=1,
            file_unique_id="uniq_leo_123",
            media_type="photo",
            source_channel_id="-1001234567890",
            source_channel_title="Meme Archives",
            added_by=12345678,
        )
        self.assertGreater(tid, 0)

        template = await get_template_by_id(tid)
        self.assertIsNotNone(template)
        self.assertEqual(template["name"], "Leonardo DiCaprio Laughing")
        self.assertEqual(template["title"], "Leonardo DiCaprio Laughing")
        self.assertEqual(template["media_type"], "photo")
        self.assertEqual(template["source_channel_id"], "-1001234567890")
        self.assertEqual(template["added_by"], 12345678)
        self.assertEqual(template["usage_count"], 0)

        # Increment usage
        await increment_template_usage(tid)
        updated = await get_template_by_id(tid)
        self.assertEqual(updated["usage_count"], 1)

        # 3. Add video template
        vid_id = await add_template(
            file_id="tg_video_id_456",
            title="Funny Dancing Cat",
            tags="cat dance funny",
            media_type="video",
            added_by=87654321,
        )
        vid_template = await get_template_by_id(vid_id)
        self.assertEqual(vid_template["media_type"], "video")

        # 4. Pagination & Count
        total_count = await get_templates_count()
        self.assertEqual(total_count, 2)
        page1 = await get_templates_paginated(limit=1, offset=0)
        self.assertEqual(len(page1), 1)

        # 5. Random template
        random_t = await get_random_template()
        self.assertIsNotNone(random_t)
        self.assertIn(random_t["id"], [tid, vid_id])

        # 6. User Preferences & Advanced Watermark CRUD
        await update_user_font(user_id=999, font_key="Kalpurush")
        await update_user_watermark_settings(
            user_id=999,
            scale=1.5,
            opacity=0.75,
            position="top_right",
            text="@mywatermark",
            enabled=1,
        )
        user = await get_user(999)
        self.assertIsNotNone(user)
        self.assertEqual(user["preferred_font"], "Kalpurush")
        self.assertEqual(user["watermark_scale"], 1.5)
        self.assertEqual(user["watermark_opacity"], 0.75)
        self.assertEqual(user["watermark_position"], "top_right")
        self.assertEqual(user["watermark_text"], "@mywatermark")
        self.assertEqual(user["watermark_enabled"], 1)

        # 7. Alias Mapping
        await add_alias("leo laugh", "Leonardo DiCaprio Laughing")
        canonical = await resolve_alias("leo laugh")
        self.assertEqual(canonical, "Leonardo DiCaprio Laughing")

        # 8. External Sources CRUD
        sid = await add_source("https://api.imgflip.com/get_memes", "Imgflip Memes")
        self.assertGreater(sid, 0)
        sources = await get_all_sources()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["url"], "https://api.imgflip.com/get_memes")
        self.assertEqual(sources[0]["name"], "Imgflip Memes")

        # Remove source
        removed = await remove_source(sid)
        self.assertTrue(removed)
        sources_after = await get_all_sources()
        self.assertEqual(len(sources_after), 0)


if __name__ == "__main__":
    unittest.main()
