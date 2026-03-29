import { useState, useEffect } from "react";
import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

const MONTH_ABBREV = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/**
 * Get a Date object adjusted for the given IANA timezone.
 * Uses Intl.DateTimeFormat to extract the parts in the target zone.
 */
function getDateInTimezone(date: Date, timezone?: string): Date {
  if (!timezone) return date;
  try {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    const parts: Record<string, string> = {};
    for (const p of fmt.formatToParts(date)) {
      parts[p.type] = p.value;
    }
    return new Date(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day),
      Number(parts.hour) === 24 ? 0 : Number(parts.hour),
      Number(parts.minute),
      Number(parts.second),
    );
  } catch {
    return date;
  }
}

/**
 * Format a Date using a simple token-based format string.
 *
 * Supported tokens:
 *   h   - 12-hour (no pad)   hh  - 12-hour (zero-padded)
 *   H   - 24-hour (no pad)   HH  - 24-hour (zero-padded)
 *   mm  - minutes (zero-padded)
 *   ss  - seconds (zero-padded)
 *   A   - AM/PM              a   - am/pm
 *   M   - month (no pad)     MM  - month (zero-padded)
 *   MMM - month abbreviated (e.g. "Jan")
 *   D   - day (no pad)       DD  - day (zero-padded)
 *   YYYY - four-digit year
 */
function formatTime(date: Date, format: string): string {
  const hours24 = date.getHours();
  const hours12 = hours24 % 12 || 12;
  const minutes = date.getMinutes();
  const seconds = date.getSeconds();
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const year = date.getFullYear();
  const ampm = hours24 < 12 ? "AM" : "PM";

  const pad = (n: number): string => (n < 10 ? `0${n}` : String(n));

  // Replace tokens from longest to shortest to avoid partial matches
  let result = format;
  result = result.replace(/YYYY/g, String(year));
  result = result.replace(/MMM/g, MONTH_ABBREV[month - 1]);
  result = result.replace(/MM/g, pad(month));
  // Single M: only replace when not preceded/followed by another M
  result = result.replace(/(?<!M)M(?!M)/g, String(month));
  result = result.replace(/DD/g, pad(day));
  result = result.replace(/(?<!D)D(?!D)/g, String(day));
  result = result.replace(/HH/g, pad(hours24));
  result = result.replace(/(?<!H)H(?!H)/g, String(hours24));
  result = result.replace(/hh/g, pad(hours12));
  result = result.replace(/(?<!h)h(?!h)/g, String(hours12));
  result = result.replace(/mm/g, pad(minutes));
  result = result.replace(/ss/g, pad(seconds));
  result = result.replace(/A/g, ampm);
  result = result.replace(/(?<!\\)a/g, ampm.toLowerCase());

  return result;
}

const DEFAULT_FORMATS: Record<string, string> = {
  time: "h:mm A",
  date: "MMM D, YYYY",
  datetime: "MMM D, YYYY h:mm A",
  countdown: "HH:mm:ss",
  elapsed: "HH:mm:ss",
  meeting: "mm:ss",
};

const STATIC_PREVIEWS: Record<string, string> = {
  time: "2:30 PM",
  date: "Jan 15, 2026",
  datetime: "Jan 15, 2026 2:30 PM",
  countdown: "00:45:00",
  elapsed: "01:23:45",
  meeting: "45:00",
};

function getFormattedTime(
  mode: string,
  format: string,
  timezone?: string,
): string {
  const now = new Date();
  const adjusted = getDateInTimezone(now, timezone);

  switch (mode) {
    case "time":
    case "date":
    case "datetime":
      return formatTime(adjusted, format);
    case "countdown":
      return "00:45:00";
    case "elapsed":
      return "01:23:45";
    case "meeting": {
      return "45:00";
    }
    default:
      return formatTime(adjusted, format);
  }
}

export function ClockRenderer({ element, previewMode }: Props) {
  const mode = element.clock_mode || "time";
  const format = element.format || DEFAULT_FORMATS[mode] || DEFAULT_FORMATS.time;
  const timezone = element.timezone;

  const [display, setDisplay] = useState(() =>
    previewMode ? getFormattedTime(mode, format, timezone) : STATIC_PREVIEWS[mode] || STATIC_PREVIEWS.time,
  );

  useEffect(() => {
    if (!previewMode) {
      setDisplay(STATIC_PREVIEWS[mode] || STATIC_PREVIEWS.time);
      return;
    }

    // Update immediately
    setDisplay(getFormattedTime(mode, format, timezone));

    // Tick every second for live modes
    if (mode === "time" || mode === "date" || mode === "datetime") {
      const interval = setInterval(() => {
        setDisplay(getFormattedTime(mode, format, timezone));
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [previewMode, mode, format, timezone]);

  const css = buildElementStyle(element.style, {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: "100%",
    height: "100%",
  });

  if (!element.style.text_color) css.color = "#ffffff";
  if (!element.style.font_size) css.fontSize = "16px";

  return (
    <div style={css}>
      {display}
    </div>
  );
}
