import os
from pathlib import Path
from typing import List, Optional

# Base directory of the project
BASE_DIR: Path = Path(__file__).resolve().parent

# Automatically load .env file if present
def _load_env_file(dotenv_path: Path) -> None:
    """Lightweight .env loader that doesn't strictly depend on python-dotenv."""
    if not dotenv_path.is_file():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(dotenv_path)
    except ImportError:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val

_load_env_file(BASE_DIR / ".env")

# Telegram Bot token from @BotFather
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

# Admin Telegram user IDs (comma or space separated)
def _parse_admin_ids(raw_ids: str) -> List[int]:
    admin_list: List[int] = []
    if not raw_ids:
        return admin_list
    for item in raw_ids.replace(",", " ").split():
        item = item.strip()
        try:
            admin_list.append(int(item))
        except ValueError:
            pass
    return admin_list

ADMIN_IDS: List[int] = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

# Authorized Channel ID for automated template ingestion (optional, e.g. -1001234567890)
def _parse_channel_id(raw_id: str) -> Optional[int]:
    val = raw_id.strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        return None

CHANNEL_ID: Optional[int] = _parse_channel_id(os.getenv("CHANNEL_ID", ""))

# Database configuration (supports relative paths and absolute persistent volume mounts)
_raw_db_path = os.getenv("DB_PATH", "data/bot.db").strip()
_parsed_db_path = Path(_raw_db_path)
DB_PATH: Path = _parsed_db_path if _parsed_db_path.is_absolute() else (BASE_DIR / _parsed_db_path)

# Assets configuration
ASSETS_DIR: Path = BASE_DIR / "assets"
LOGOS_DIR: Path = ASSETS_DIR / "logos"
FONTS_DIR: Path = ASSETS_DIR / "fonts"

# Brand Logo Paths (checking canonical snake_case first, fallback to spaces)
WHITE_LOGO_PATH: Path = LOGOS_DIR / "kbkh_white.png"
if not WHITE_LOGO_PATH.exists() and (LOGOS_DIR / "kbkh white logo.png").exists():
    WHITE_LOGO_PATH = LOGOS_DIR / "kbkh white logo.png"

BLACK_LOGO_PATH: Path = LOGOS_DIR / "kbkh_black.png"
if not BLACK_LOGO_PATH.exists() and (LOGOS_DIR / "kbkh black logo.png").exists():
    BLACK_LOGO_PATH = LOGOS_DIR / "kbkh black logo.png"

# Default primary font identifiers
DEFAULT_BENGALI_FONT: str = "Kalpurush.ttf"
DEFAULT_ENGLISH_FONT: str = "Impact.ttf"

# Luminance threshold for auto-contrast logo selection (0 to 255 scale)
# Formula: 0.299R + 0.587G + 0.114B
# If luminance < threshold -> Dark background -> Use White logo
# If luminance >= threshold -> Light background -> Use Black logo
LUMINANCE_THRESHOLD: float = 128.0

def validate_config() -> None:
    """Validate critical environment configurations on bot startup."""
    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN is not configured! Please set BOT_TOKEN in .env or environment variables."
        )
    if not WHITE_LOGO_PATH.exists():
        raise FileNotFoundError(f"White brand logo not found at {WHITE_LOGO_PATH}")
    if not BLACK_LOGO_PATH.exists():
        raise FileNotFoundError(f"Black brand logo not found at {BLACK_LOGO_PATH}")
    if not FONTS_DIR.exists():
        raise FileNotFoundError(f"Fonts directory not found at {FONTS_DIR}")
