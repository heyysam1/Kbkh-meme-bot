"""Regression tests for callback wiring bugs fixed 2026-10-09.

Locks in:
- No `or` inside @router.callback_query(...) filters (Python `or` collapses
  `F.data == "a" or F.data == "b"` to just the first filter at decoration time,
  silently killing the second alternative).
- Every callback_data emitted on an InlineKeyboardButton has a matching
  registered callback_query filter (the /start dashboard dead-button bug).
- The dead first-generation meme_flow module stays deleted.
"""
import glob
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _iter_py_files():
    for pattern in ("handlers/*.py", "services/*.py"):
        for f in glob.glob(str(REPO_ROOT / pattern)):
            yield Path(f)


def _collect_filters():
    """Return list of (file, filter_text) for every callback_query decorator."""
    out = []
    for f in _iter_py_files():
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"@\w+\.callback_query\((.*?)\)\s*\n", src, re.S):
            out.append((f.name, re.sub(r"\s+", " ", m.group(1)).strip()))
    return out


def _collect_buttons():
    """Return list of (file, callback_data template) for every button."""
    out = []
    for f in _iter_py_files():
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r'callback_data=(f?"[^"]*")', src):
            v = m.group(1)
            if v.startswith("f"):
                v = re.sub(r"\{[^}]*\}", "X", v[1:])
            out.append((f.name, v.strip('"')))
    return out


def _filter_matches_button(button: str, filt: str) -> bool:
    for lit in re.findall(r'"([^"]+)"', filt):
        if lit.endswith(":"):
            if button.startswith(lit):
                return True
        elif lit == button:
            return True
    return False


class TestCallbackWiring(unittest.TestCase):
    def test_no_python_or_in_callback_filters(self):
        """`or` inside a callback_query filter silently drops all but the first alternative."""
        bad = []
        for f in _iter_py_files():
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if "callback_query(" in line and " or F.data" in line:
                    bad.append(f"{f.name}:{i}: {line.strip()}")
        self.assertEqual(bad, [], f"Found `or` in callback filters (use `|`): {bad}")

    def test_every_button_has_a_matching_handler(self):
        """Every callback_data on a button must match at least one registered filter."""
        filters = _collect_filters()
        unhandled = sorted(
            {b for _, b in _collect_buttons() if not any(_filter_matches_button(b, flt) for _, flt in filters)}
        )
        self.assertEqual(unhandled, [], f"Buttons with no matching handler: {unhandled}")

    def test_start_dashboard_buttons_are_wired(self):
        """The /start dashboard buttons that were dead must resolve to live handlers."""
        from handlers.start import get_main_menu_keyboard

        kb = get_main_menu_keyboard()
        emitted = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        self.assertIn("tpl_grid:1", emitted)
        self.assertIn("action_add_template", emitted)
        self.assertNotIn("cb_grid_page:1", emitted)
        self.assertNotIn("cb_add_template", emitted)

        filters = _collect_filters()
        for data in ("tpl_grid:1", "action_add_template"):
            self.assertTrue(
                any(_filter_matches_button(data, flt) for _, flt in filters),
                f"No handler matches dashboard button {data!r}",
            )

    def test_random_again_button_is_wired(self):
        """[Another Random] must have a matching handler."""
        filters = _collect_filters()
        self.assertTrue(
            any(_filter_matches_button("btn_random_again", flt) for _, flt in filters),
            "No handler matches btn_random_again",
        )

    def test_editor_alias_buttons_are_wired(self):
        """ed_cycle_case / ed_toggle_clean / edit:banner:* buttons must have handlers."""
        filters = _collect_filters()
        for data in ("ed_cycle_case", "ed_toggle_clean", "edit:banner:menu", "edit:banner:apply:3", "edit:banner:remove"):
            self.assertTrue(
                any(_filter_matches_button(data, flt) for _, flt in filters),
                f"No handler matches editor button {data!r}",
            )

    def test_meme_flow_module_stays_deleted(self):
        """The superseded first-generation flow was removed; it must not come back."""
        self.assertFalse(
            (REPO_ROOT / "handlers" / "meme_flow.py").exists(),
            "handlers/meme_flow.py was re-added; the editor flow owns meme creation now",
        )
        import bot  # noqa: F401  (import must succeed without the module)

    def test_no_meme_flow_references(self):
        """No Python file may reference the deleted meme_flow module."""
        refs = []
        for f in _iter_py_files():
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if "meme_flow" in line:
                    refs.append(f"{f.name}:{i}")
        self.assertEqual(refs, [], f"Stale meme_flow references: {refs}")


if __name__ == "__main__":
    unittest.main()
