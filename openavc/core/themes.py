"""
The theme rule: which themes there are and where each is looked up, what a
theme file may hold, the variables a theme sets and what the panel draws for
one a theme leaves out, the project settings that paint over a theme, and
whether a theme is light or dark.

- **Lookup.** A theme id names a built-in theme (``openavc/themes/``, shipped
  with the application) or a custom one (``themes/`` beside the project file),
  the built-in first, so a custom file can never stand in for a built-in. A
  file that cannot be read as a JSON object is no theme. An id that would leave
  the themes folder is refused (:class:`ThemeRefused`). The REST routes
  (``api/themes.py``), the AI's theme tools (``cloud/tools/system_tools.py``)
  and the walk of what still shows an uploaded file (``api/assets.py``) all
  look a theme up here.
- **Shape.** :func:`check_theme` is what a theme must be before it is saved or
  imported: a JSON object with a name, a version, an id the lookup can find
  (``ID_PATTERN``) and its variables as named values, whose page picture, when
  it has one, is text. Each refusal's text is the sentence to show.
- **Variables.** ``VARIABLES`` is every variable a theme sets: the CSS custom
  property the panel puts it on (none for the font, which goes on the page
  body) and what the panel draws when a theme leaves it out (its stylesheets'
  ``:root`` and body font, held to this table by a test).
- **Settings over a theme.** ``SETTINGS_OVER_THEME`` is the project settings
  that paint over a theme's variables, in the order they apply, and the
  variable each one sets (``theme_overrides`` names its own). A blank one
  leaves the theme's.
- **Light or dark.** :func:`theme_mode` is the treatment a project records
  beside a chosen theme, which the panel falls back on when the theme cannot be
  had: by the luminance of the theme's ``panel_bg``, read as the panel reads a
  colour (``web/panel/modules/lit.js``: hex or ``rgb()``), and by its id only
  when that colour cannot be read. Its two words (``MODES``) are the ones the
  project schema maps to the theme a project with no theme id draws
  (``UISettings.BLANK_FROM``).

:func:`theme_values` is the table the panel (``modules/theme_values.gen.js``)
and the Builder (``api/themeValues.gen.ts``) read, rendered by
``python -m openavc.core.themes_gen``.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Union

from openavc.system_config import THEMES_DIR as BUILTIN_DIR
from openavc.utils.logger import get_logger
from openavc.utils.paths import safe_path_within

log = get_logger(__name__)

__all__ = [
    "BUILTIN_DIR", "CustomDir", "ID_PATTERN", "MODES", "SETTINGS_OVER_THEME", "VARIABLES",
    "SettingOverTheme", "ThemeRefused", "ThemeVariable", "all_themes",
    "check_theme", "find_theme", "is_builtin", "project_themes_dir",
    "relative_luminance", "rgb_channels", "theme_file", "theme_mode",
    "theme_values",
]

#: A theme id, which is also its file's name without ``.json``.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")

#: What every theme file carries, in the order a refusal names them.
REQUIRED_FIELDS = ("name", "id", "version", "variables")

_BAD_ID = "Theme ID must be lowercase alphanumeric with hyphens"
_ID_OUTSIDE = "Invalid theme id"
_NOT_A_THEME = "A theme file holds one JSON object with the theme's name, id, version and variables."
_VARIABLES_NOT_NAMED = (
    "The theme's variables must be named values, such as \"panel_bg\": \"#1a1a2e\"."
)
_PICTURE_NOT_TEXT = (
    "The theme's page background picture is not a picture name or web address. "
    "Choose the picture again, or clear it."
)


class ThemeRefused(ValueError):
    """A theme id or theme file that is not taken; its text is the sentence to show."""


@dataclass(frozen=True)
class ThemeVariable:
    """One variable a theme sets."""

    #: The key in a theme's ``variables``.
    name: str
    #: The CSS custom property the panel puts it on; None for the font, which
    #: the panel puts on the page body.
    css_property: str | None
    #: What the panel draws when the theme leaves it out.
    fallback: str
    #: ``color``, ``length`` (a number is rem) or ``font``.
    kind: str


#: Every variable a theme sets, in the order the panel puts them on.
VARIABLES: tuple[ThemeVariable, ...] = (
    ThemeVariable("panel_bg", "--panel-bg", "#1a1a2e", "color"),
    ThemeVariable("panel_text", "--panel-text", "#ffffff", "color"),
    ThemeVariable("accent", "--panel-accent", "#2196F3", "color"),
    ThemeVariable("button_bg", "--panel-button-bg", "#424242", "color"),
    ThemeVariable("button_text", "--panel-button-text", "#cccccc", "color"),
    ThemeVariable("button_border", "--panel-button-border", "#555555", "color"),
    ThemeVariable("danger", "--panel-danger", "#F44336", "color"),
    ThemeVariable("success", "--panel-success", "#4CAF50", "color"),
    ThemeVariable("warning", "--panel-warning", "#FF9800", "color"),
    ThemeVariable("surface", "--panel-surface", "#2a2a4a", "color"),
    ThemeVariable("surface_border", "--panel-surface-border", "#3a3a5c", "color"),
    ThemeVariable("border_radius", "--panel-border-radius", "0.5714rem", "length"),
    ThemeVariable("font_family", None, "'Inter', system-ui, -apple-system, sans-serif", "font"),
)


@dataclass(frozen=True)
class SettingOverTheme:
    """A project setting that paints over the theme's variables."""

    #: The setting in a project's ``ui.settings``.
    setting: str
    #: The variable it sets; None for a setting that is itself a map of
    #: variables to values.
    variable: str | None
    #: What the setting holds when it is blank, leaving the theme's own.
    blank: Any


#: The settings over a theme, in the order they apply: each one a later entry
#: names wins over the theme's variables and the entries before it.
SETTINGS_OVER_THEME: tuple[SettingOverTheme, ...] = (
    SettingOverTheme("theme_overrides", None, {}),
    SettingOverTheme("accent_color", "accent", ""),
    SettingOverTheme("font_family", "font_family", ""),
)


# --- The lookup ---------------------------------------------------------------


def project_themes_dir(project_path: str | Path) -> Path:
    """The custom themes folder beside a project file, made if it is not there."""
    themes_dir = Path(project_path).parent / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    return themes_dir


def theme_file(base: Path, theme_id: str) -> Path:
    """``<base>/<theme_id>.json``, refused when the id would leave ``base``."""
    path = safe_path_within(base, f"{theme_id}.json") if isinstance(theme_id, str) else None
    if path is None:
        raise ThemeRefused(_ID_OUTSIDE)
    return path


def is_builtin(theme_id: str) -> bool:
    """Whether a built-in theme has this id."""
    return theme_file(BUILTIN_DIR, theme_id).exists()


#: The custom themes folder, or what finds it only when a lookup gets past the
#: built-ins (an open route answers a built-in theme without the project).
CustomDir = Union[Path, Callable[[], Path], None]


def _places(custom_dir: CustomDir) -> Iterator[tuple[str, Path]]:
    """Where themes are looked up, in order, with the source each one is."""
    yield "builtin", BUILTIN_DIR
    folder = custom_dir() if callable(custom_dir) else custom_dir
    if folder is not None:
        yield "custom", folder


def _read(path: Path, source: str) -> dict[str, Any] | None:
    """The theme in a file, marked with its source; None when it is no theme."""
    try:
        theme = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning(f"Theme file {path.name} could not be read: {exc}")
        return None
    if not isinstance(theme, dict):
        log.warning(f"Theme file {path.name} holds no theme")
        return None
    theme["_source"] = source
    return theme


def find_theme(theme_id: str, custom_dir: CustomDir) -> dict[str, Any] | None:
    """The theme with this id, built-in first, then custom; None when there is none."""
    for source, base in _places(custom_dir):
        path = theme_file(base, theme_id)
        if path.exists():
            return _read(path, source)
    return None


def _themes_in(source: str, base: Path) -> list[dict[str, Any]]:
    """The themes in one folder, by file name; none when there is no folder."""
    if not base.is_dir():
        return []
    read = (_read(path, source) for path in sorted(base.glob("*.json")))
    return [theme for theme in read if theme is not None]


def all_themes(custom_dir: CustomDir) -> list[dict[str, Any]]:
    """Every theme there is: the built-ins, then the custom ones, each by file name."""
    return [theme for source, base in _places(custom_dir) for theme in _themes_in(source, base)]


# --- The shape ------------------------------------------------------------------


def _resolve_variable(value: Any, variables: dict[str, Any]) -> Any:
    """A ``var(name)`` reference's value, as the panel resolves one (None when
    the theme has no such variable); any other value as it is."""
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"var\(([^)]+)\)", value)
    if not match:
        return value
    return variables.get(match.group(1).strip())


def _check_page_picture(data: dict[str, Any]) -> None:
    defaults = data.get("page_defaults")
    if not isinstance(defaults, dict):
        return
    picture = _resolve_variable(defaults.get("background_image"), data["variables"])
    if picture is not None and not isinstance(picture, str):
        raise ThemeRefused(_PICTURE_NOT_TEXT)


def _check_fields(data: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ThemeRefused(f"Missing required fields: {', '.join(missing)}")


def _check_id(data: dict[str, Any]) -> None:
    theme_id = data["id"]
    if not isinstance(theme_id, str) or not ID_PATTERN.match(theme_id):
        raise ThemeRefused(_BAD_ID)


def _check_variables(data: dict[str, Any]) -> None:
    if not isinstance(data["variables"], dict):
        raise ThemeRefused(_VARIABLES_NOT_NAMED)


#: What a theme is checked for, in order: each later check reads what the
#: earlier ones made sure of.
_CHECKS = (_check_fields, _check_id, _check_variables, _check_page_picture)


def check_theme(data: Any) -> None:
    """Refuse a theme that cannot be saved or imported as it is."""
    if not isinstance(data, dict):
        raise ThemeRefused(_NOT_A_THEME)
    for check in _CHECKS:
        check(data)


# --- Light or dark ----------------------------------------------------------------

LIGHT = "light"
DARK = "dark"
MODES = (LIGHT, DARK)

#: A page background brighter than this is a light theme.
_LIGHT_ABOVE = 0.5
#: An id that reads as light when its background cannot be: one with "light"
#: in it, or one of these.
_LIGHT_IDS = frozenset({"minimal"})

_HEX = re.compile(r"#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})")
_FUNCTION = re.compile(r"rgba?\(([^)]+)\)")
# The leading number JavaScript's parseFloat reads from a channel.
_LEADING_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?")


def _leading_number(text: str) -> float | None:
    match = _LEADING_NUMBER.match(text)
    return float(match.group(0)) if match else None


def _channel(text: str) -> int | None:
    """One channel, 0-255, from a number or a percentage; None when it is not a number."""
    number = _leading_number(text)
    if number is None:
        return None
    if text.endswith("%"):
        number = number / 100 * 255
    return math.floor(max(0.0, min(255.0, number)) + 0.5)


def _hex_channels(digits: str) -> tuple[int, int, int]:
    wide = len(digits) > 4
    pairs = [digits[i * 2:i * 2 + 2] if wide else digits[i] * 2 for i in range(3)]
    r, g, b = (int(pair, 16) for pair in pairs)
    return r, g, b


def _function_channels(inside: str) -> tuple[int, int, int] | None:
    parts = [part for part in re.split(r"[,/\s]+", inside) if part]
    if len(parts) < 3:
        return None
    channels = [_channel(part) for part in parts[:3]]
    if None in channels:
        return None
    r, g, b = channels
    return r, g, b


def rgb_channels(colour: Any) -> tuple[int, int, int] | None:
    """Red, green and blue (0-255) of a hex or ``rgb()``/``rgba()`` colour, or None."""
    if not isinstance(colour, str):
        return None
    text = colour.strip().lower()
    hex_match = _HEX.fullmatch(text)
    if hex_match:
        return _hex_channels(hex_match.group(1))
    function = _FUNCTION.fullmatch(text)
    return _function_channels(function.group(1)) if function else None


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance of red, green and blue (0-255)."""
    def linear(channel: int) -> float:
        s = channel / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _reads_light(theme_id: str) -> bool:
    return "light" in theme_id or theme_id in _LIGHT_IDS


def _mode_by_id(theme_id: Any) -> str:
    if not isinstance(theme_id, str):
        return DARK
    return LIGHT if _reads_light(theme_id) else DARK


def theme_mode(theme: dict[str, Any]) -> str:
    """``light`` or ``dark``: the treatment a project records beside this theme."""
    variables = theme.get("variables")
    background = variables.get("panel_bg") if isinstance(variables, dict) else None
    rgb = rgb_channels(background)
    if rgb is None:
        return _mode_by_id(theme.get("id"))
    return LIGHT if relative_luminance(rgb) > _LIGHT_ABOVE else DARK


# --- The table the panel and the Builder read -------------------------------------


def theme_values() -> dict[str, Any]:
    """The variables and the settings over a theme, as the generated files carry them."""
    return {
        "variables": [asdict(variable) for variable in VARIABLES],
        "settings_over_theme": [asdict(setting) for setting in SETTINGS_OVER_THEME],
    }
