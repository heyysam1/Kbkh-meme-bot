import os
import tempfile
import unittest
from pathlib import Path
import config
from database.db import get_db, init_db
from database.queries import (
    add_template,
    get_template_by_id,
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
        # 1. Initialize schema
        await init_db()
        self.assertTrue(self.test_db_path.exists())

        # 2. Template CRUD
        tid = await add_template("tg_file_id_123", "Leonardo DiCaprio Laughing", "django leonardo drink laugh", is_trending=1)
        self.assertGreater(tid, 0)

        template = await get_template_by_id(tid)
        self.assertIsNotNone(template)
        self.assertEqual(template["name"], "Leonardo DiCaprio Laughing")
        self.assertEqual(template["usage_count"], 0)

        await increment_template_usage(tid)
        updated = await get_template_by_id(tid)
        self.assertEqual(updated["usage_count"], 1)

        # 3. User Preferences CRUD
        await update_user_font(user_id=999, font_key="Kalpurush")
        user = await get_user(999)
        self.assertIsNotNone(user)
        self.assertEqual(user["preferred_font"], "Kalpurush")

        # 4. Alias Mapping
        await add_alias("leo laugh", "Leonardo DiCaprio Laughing")
        canonical = await resolve_alias("leo laugh")
        self.assertEqual(canonical, "Leonardo DiCaprio Laughing")


if __name__ == "__main__":
    unittest.main()
