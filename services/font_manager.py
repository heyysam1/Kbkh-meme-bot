import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import ImageFont
import config

class FontManager:
    """
    Dynamic typography management subsystem for KBKH Meme Bot.
    Scans, sanitizes, and serves verified Unicode fonts with script-aware fallbacks.
    """

    def __init__(self, fonts_dir: Optional[Path] = None):
        self.fonts_dir = fonts_dir or config.FONTS_DIR
        self._fonts: Dict[str, Path] = {}
        self._display_names: Dict[str, str] = {}
        self.scan_fonts()

    def sanitize_display_name(self, filename: str) -> str:
        """Convert a font filename into a clean, human-readable display title for UI buttons."""
        name = Path(filename).stem
        # Remove common technical suffixes
        name = name.replace("Unicode", "").replace("Regular", "").replace("-", " ")
        # Clean extra spaces
        name = re.sub(r"\s+", " ", name).strip()

        # Specific custom beauty mappings
        custom_titles = {
            "HindSiliguri Bold": "Hind Siliguri (Bold)",
            "kalpurush": "Kalpurush",
            "Anek Bangla ExtraBold": "Anek Bangla (ExtraBold)",
            "Anek Bangla Condensed Bold": "Anek Bangla (Condensed)",
            "Headline Bangla": "Headline Bangla",
            "Li Ador Noirrit A V2 Italic": "Li Ador Noirrit (Italic)",
            "Li Saboj Charulota Medium": "Li Saboj Charulota",
            "LiShamimCholontika": "Li Shamim Cholontika",
            "Lima Bosonto Borno Encoding": "Lima Bosonto Borno",
            "Noto Sans Bengali Thin": "Noto Sans Bengali (Thin)",
            "Impact": "Impact (Classic Meme)",
            "Anton": "Anton (Bold Impact)",
            "Poppins Bold": "Poppins (Bold)",
            "Inter Bold": "Inter (Bold)",
            "BebasNeue": "Bebas Neue",
            "Oswald Bold": "Oswald (Bold)",
            "Montserrat Bold": "Montserrat (Bold)",
        }
        return custom_titles.get(name, name)

    def scan_fonts(self) -> Dict[str, Path]:
        """Scan fonts directory, registering all valid .ttf/.otf fonts while ignoring ANSI/corrupt files."""
        self._fonts.clear()
        self._display_names.clear()

        if not self.fonts_dir.is_dir():
            return self._fonts

        # Banned/Purged font stems (known non-Unicode or broken fonts)
        banned_stems = {"li-shakib75-ansi-v2", "ansi"}

        for item in sorted(os.listdir(self.fonts_dir)):
            if not item.lower().endswith((".ttf", ".otf")):
                continue

            stem = Path(item).stem.lower()
            if any(banned in stem for banned in banned_stems):
                continue

            font_path = self.fonts_dir / item
            try:
                # Test font loading to ensure it is not corrupt
                ImageFont.truetype(str(font_path), 20)
                font_key = item
                self._fonts[font_key] = font_path
                self._display_names[font_key] = self.sanitize_display_name(item)
            except Exception:
                # Skip invalid or corrupt font files defensively
                continue

        return self._fonts

    def detect_script(self, text: str) -> str:
        """
        Detect whether the supplied text contains Bengali Unicode characters.
        Range: U+0980 to U+09FF (Bengali block).
        """
        for char in text:
            if "\u0980" <= char <= "\u09ff":
                return "bengali"
        return "english"

    def get_font_path(self, font_key: Optional[str] = None, script: str = "bengali") -> Path:
        """
        Resolve the absolute font path from a user key, falling back gracefully
        to default script typography if missing or unrecognized.
        """
        if font_key and font_key in self._fonts:
            return self._fonts[font_key]

        # Script-aware default fallbacks
        if script == "bengali":
            default_name = config.DEFAULT_BENGALI_FONT
        else:
            default_name = config.DEFAULT_ENGLISH_FONT

        if default_name in self._fonts:
            return self._fonts[default_name]

        # Universal fallback to any discovered font, or system font
        if self._fonts:
            return next(iter(self._fonts.values()))

        raise FileNotFoundError("No valid fonts found in assets directory.")

    def get_font_list(self) -> List[Tuple[str, str]]:
        """Return list of (font_key, display_name) for inline keyboard generation."""
        if not self._fonts:
            self.scan_fonts()
        return [(k, self._display_names[k]) for k in self._fonts.keys()]

    def load_font(self, font_path: Path, size: int) -> ImageFont.FreeTypeFont:
        """Defensively load ImageFont with fallback to default fonts on error."""
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception:
            try:
                fallback_path = self.get_font_path(config.DEFAULT_BENGALI_FONT)
                return ImageFont.truetype(str(fallback_path), size)
            except Exception:
                return ImageFont.load_default()

# Singleton font manager instance
font_manager = FontManager()
