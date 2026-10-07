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
    increment_template_usage,
    update_user_font,
    get_user,
    add_alias,
    resolve_alias,
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

        # 2. Template CRUD with multi-mime & metadata
        tid = await add_template(
            file_id="tg_file_id_123",
            name="Leonardo DiCaprio Laughing",
            tags="django leonardo drink laugh",
            is_trending=1,
            file_unique_id="uniq_leo_123",
            media_type="photo",
            source_channel_id="-1001234567890",
            source_channel_title="Meme Archives",
        )
        self.assertGreater(tid, 0)

        template = await get_template_by_id(tid)
        self.assertIsNotNone(template)
        self.assertEqual(template["name"], "Leonardo DiCaprio Laughing")
        self.assertEqual(template["title"], "Leonardo DiCaprio Laughing")
        self.assertEqual(template["media_type"], "photo")
        self.assertEqual(template["source_channel_id"], "-1001234567890")
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
        )
        vid_template = await get_template_by_id(vid_id)
        self.assertEqual(vid_template["media_type"], "video")

        # 4. Random template
        random_t = await get_random_template()
        self.assertIsNotNone(random_t)
        self.assertIn(random_t["id"], [tid, vid_id])

        # 5. User Preferences CRUD
        await update_user_font(user_id=999, font_key="Kalpurush")
        user = await get_user(999)
        self.assertIsNotNone(user)
        self.assertEqual(user["preferred_font"], "Kalpurush")

        # 6. Alias Mapping
        await add_alias("leo laugh", "Leonardo DiCaprio Laughing")
        canonical = await resolve_alias("leo laugh")
        self.assertEqual(canonical, "Leonardo DiCaprio Laughing")


if __name__ == "__main__":
    unittest.main()
