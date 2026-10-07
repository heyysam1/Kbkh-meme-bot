import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from PIL import ImageFont
import config

logger = logging.getLogger("kbkh_meme_bot.font_manager")

# Strictly defined curated font definitions (Exactly 10 permitted fonts)
# Each entry: (canonical_key, display_name, script, alternate_filenames)
CURATED_FONTS: List[Tuple[str, str, str, List[str]]] = [
    ("Kalpurush.ttf", "Kalpurush", "bengali", ["kalpurush.ttf"]),
    ("AnekBangla.ttf", "Anek Bangla", "bengali", ["AnekBangla-Regular.ttf", "Anek Bangla.ttf"]),
    ("AnekBangla-ExtraBold.ttf", "Anek ExtraBold", "bengali", ["Anek Bangla ExtraBold.ttf"]),
    ("LiSiliguri.ttf", "Li Siliguri", "bengali", ["HindSiliguri.ttf", "HindSiliguri-Bold.ttf", "LiSiliguri.ttf"]),
    ("HeadlineBangla.ttf", "Headline Bangla", "bengali", ["Li_Headline.ttf", "Headline Bangla Regular Unicode.ttf"]),
    ("NotoSansBengali.ttf", "Noto Sans Bengali", "bengali", ["NotoSansBengali-Bold.ttf", "Noto_Sans_Bengali-Thin.ttf"]),
    ("Impact.ttf", "Impact", "english", []),
    ("Anton-Regular.ttf", "Anton", "english", ["Anton.ttf"]),
    ("Inter-Bold.ttf", "Inter", "english", ["Inter.ttf"]),
    ("Poppins-Bold.ttf", "Poppins Bold", "english", ["Poppins.ttf"]),
]


class FontManager:
    """
    Strict typography management subsystem for KBKH Meme Bot.
    Enforces the exact 10 curated fonts and provides Bengali Raqm text layout.
    """

    def __init__(self, fonts_dir: Optional[Path] = None):
        self.fonts_dir = fonts_dir or config.FONTS_DIR
        self._fonts: Dict[str, Path] = {}
        self._display_names: Dict[str, str] = {}
        self.scan_fonts()

    def sanitize_display_name(self, filename_or_key: str) -> str:
        """Return clean, emoji-free display name for an approved font."""
        target = Path(filename_or_key).name.lower()
        # 1. Exact match on filename or alias
        for key, display, _, aliases in CURATED_FONTS:
            all_names = [key.lower()] + [a.lower() for a in aliases]
            if target in all_names:
                return display

        # 2. Match on stem equality
        target_stem = Path(filename_or_key).stem.lower()
        for key, display, _, aliases in CURATED_FONTS:
            stems = [Path(key).stem.lower()] + [Path(a).stem.lower() for a in aliases]
            if target_stem in stems:
                return display

        return filename_or_key

    def scan_fonts(self) -> Dict[str, Path]:
        """
        Scan font directory and register strictly the 10 permitted fonts.
        All unapproved fonts are ignored.
        """
        self._fonts.clear()
        self._display_names.clear()

        if not self.fonts_dir.is_dir():
            return self._fonts

        existing_files = {f.lower(): f for f in os.listdir(self.fonts_dir)}

        for canonical_key, display_name, _, aliases in CURATED_FONTS:
            matched_file = None
            # Check canonical filename first
            if canonical_key.lower() in existing_files:
                matched_file = existing_files[canonical_key.lower()]
            else:
                # Check permitted aliases
                for alias in aliases:
                    if alias.lower() in existing_files:
                        matched_file = existing_files[alias.lower()]
                        break

            if matched_file:
                font_path = self.fonts_dir / matched_file
                try:
                    ImageFont.truetype(str(font_path), 20)
                    self._fonts[canonical_key] = font_path
                    self._display_names[canonical_key] = display_name
                except Exception as e:
                    logger.warning(f"Could not load approved font {matched_file}: {e}")

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
        Resolve font path from key or script-aware default.
        Only resolves from the 10 curated fonts.
        """
        if font_key:
            # Direct match on canonical key
            if font_key in self._fonts:
                return self._fonts[font_key]
            # Match on filename or alias
            target = Path(font_key).name.lower()
            target_norm = target.replace("_", "").replace("-", "")
            for c_key, _, _, aliases in CURATED_FONTS:
                stems = [Path(c_key).stem.lower()] + [Path(a).stem.lower() for a in aliases]
                stems_norm = [s.replace("_", "").replace("-", "").replace(" ", "") for s in stems]
                if (
                    target == c_key.lower()
                    or any(target == a.lower() for a in aliases)
                    or any(target == s for s in stems)
                    or any(target_norm == sn for sn in stems_norm)
                ):
                    if c_key in self._fonts:
                        return self._fonts[c_key]

        # Script-aware default fallbacks
        if script == "bengali":
            default_key = config.DEFAULT_BENGALI_FONT
        else:
            default_key = config.DEFAULT_ENGLISH_FONT

        if default_key in self._fonts:
            return self._fonts[default_key]

        # Fallback to any loaded font
        if self._fonts:
            return next(iter(self._fonts.values()))

        raise FileNotFoundError(f"No valid approved fonts found in {self.fonts_dir}")

    def get_font_list(self) -> List[Tuple[str, str]]:
        """
        Return list of (font_key, display_name) for inline keyboard generation.
        Strictly contains only the 10 approved fonts in curated order.
        """
        if not self._fonts:
            self.scan_fonts()
        font_list = []
        for canonical_key, display_name, _, _ in CURATED_FONTS:
            if canonical_key in self._fonts:
                font_list.append((canonical_key, display_name))
        return font_list

    def load_font(self, font_path: Union[str, Path], size: int) -> ImageFont.FreeTypeFont:
        """
        Defensively load ImageFont enforcing RAQM complex text-shaping engine
        to guarantee correct vowel-sign placement and Bengali conjunct ligatures.
        """
        try:
            return ImageFont.truetype(str(font_path), size=size, layout_engine=ImageFont.Layout.RAQM)
        except Exception as e:
            logger.warning(f"RAQM engine unavailable, falling back: {e}")
            try:
                return ImageFont.truetype(str(font_path), size=size)
            except Exception:
                return ImageFont.load_default()


# Singleton font manager instance
font_manager = FontManager()
