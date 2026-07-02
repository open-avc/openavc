// Pure logic behind the UI Builder icon picker (no React imports) so the
// node-harness regression suite can exercise it directly.
//
// The panel runtime renders icons by direct sprite lookup (icons.svg#<name>),
// so every name the picker can store must exist as a symbol id in
// web/panel/icons.svg. The harness test (tests/test_icon_picker.py) enforces
// that for the All tab and every curated category entry.
import { SPRITE_ICON_NAMES } from "./spriteIconNames";

// AV-relevant icon categories. Names must be sprite symbol ids — lucide-react
// also exports legacy aliases (tv-2, unlock, home, ...) that preview fine in
// the builder but don't exist in the sprite, so the panel renders them blank.
export const ICON_CATEGORIES: Record<string, string[]> = {
  "Power & System": [
    "power", "power-off", "plug", "zap", "shield", "lock", "lock-open", "settings",
    "settings-2", "cog", "wrench", "toggle-left", "toggle-right",
  ],
  "Audio": [
    "volume", "volume-1", "volume-2", "volume-x", "mic", "mic-off", "headphones",
    "speaker", "music", "music-2", "music-3", "audio-lines",
  ],
  "Video": [
    "monitor", "tv", "tv-minimal", "projector", "camera", "video", "video-off", "film",
    "screen-share", "screen-share-off", "airplay", "cast", "presentation",
  ],
  "Playback": [
    "play", "pause", "square", "skip-forward", "skip-back", "rewind",
    "fast-forward", "repeat", "repeat-1", "shuffle", "circle-play",
    "circle-pause", "circle-stop",
  ],
  "Navigation": [
    "arrow-up", "arrow-down", "arrow-left", "arrow-right",
    "chevron-up", "chevron-down", "chevron-left", "chevron-right",
    "chevrons-up", "chevrons-down", "chevrons-left", "chevrons-right",
    "house", "menu", "grid-3x3", "layout-grid", "maximize", "minimize",
    "move", "corner-up-left", "corner-up-right",
  ],
  "Lighting": [
    "sun", "moon", "lamp", "lamp-desk", "lamp-floor", "lightbulb",
    "sunrise", "sunset", "eye", "eye-off", "sun-dim",
  ],
  "Communication": [
    "phone", "phone-off", "phone-call", "wifi", "wifi-off", "bluetooth",
    "radio", "signal", "satellite", "globe",
  ],
  "Climate": [
    "thermometer", "thermometer-sun", "thermometer-snowflake",
    "fan", "wind", "cloud", "droplets", "snowflake",
  ],
  "Security": [
    "shield", "shield-check", "key", "scan", "fingerprint",
    "alarm-clock", "siren", "lock", "lock-open", "camera",
  ],
  "General": [
    "check", "x", "triangle-alert", "info", "circle-help", "clock",
    "calendar", "bell", "bell-off", "bookmark", "star", "heart",
    "thumbs-up", "thumbs-down", "plus", "minus", "hash",
    "circle", "square", "triangle", "diamond",
  ],
};

// All icon names offered by the "All" tab: the sprite's own symbol ids.
// Never re-derive these from lucide-react's PascalCase exports — the reverse
// kebab conversion is ambiguous for digit-containing names (Building2 could
// be "building2" or "building-2"; Grid3x3 vs ArrowDown01 split digits
// differently), and it drifted from the sprite for ~110 icons.
export const ALL_ICONS: string[] = SPRITE_ICON_NAMES;

// Kebab sprite id -> PascalCase lucide-react export name, for the builder
// preview. Unlike the reverse direction, this mapping is well-defined.
export function kebabToPascal(name: string): string {
  return name
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}
