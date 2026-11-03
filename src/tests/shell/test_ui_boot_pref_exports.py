"""Settings pref modules call these CuttleUiBoot exports; losing one blanks a Settings section."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UI_BOOT = (ROOT / "src/web/js/shared/ui_boot.js").read_text(encoding="utf-8")


def _exports() -> str:
    start = UI_BOOT.index("window.CuttleUiBoot = {")
    return UI_BOOT[start:UI_BOOT.index("\n    };", start)]


def test_settings_pref_modules_have_their_boot_exports():
    exports = _exports()
    for module, names in {
        "settings_chip_prefs.js": ("chipPrefs", "setChipPref"),
        "settings_text_prefs.js": ("textAreas", "setTextPref"),
    }.items():
        src = (ROOT / "src/web/js/settings" / module).read_text(encoding="utf-8")
        for name in names:
            assert re.search(r"boot\." + name + r"\b", src), (module, name)
            assert re.search(r"^\s+" + name + r":", exports, re.M), f"CuttleUiBoot.{name} missing (used by {module})"
