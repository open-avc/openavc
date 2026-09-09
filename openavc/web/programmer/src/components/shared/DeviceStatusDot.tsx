interface DeviceStatusDotProps {
  connected: boolean;
  orphaned?: boolean;
  /** Paused for driver testing (device.<id>.paused) — auto-reconnect is
   *  suspended, so show a distinct state instead of a plain "offline". */
  paused?: boolean;
  /** Away because a command restarted it (device.<id>.restarting) — absence
   *  we asked for, coming back on its own, so not the red a fault gets. */
  restarting?: boolean;
  size?: number;
}

/** The rule this dot keeps: GREEN means you can drive this device right now.
 *  Anything else is amber or red, and the tooltip (plus the card's own banner)
 *  says which of the four it is.
 *
 *  Paused used to take `--color-info` for that middle band, on the reasonable
 *  assumption that the token was a blue — its fallback is `#6aa3d6`. It is not:
 *  `tokens.css` defines it as `#8AB493`, the brand sage, which is close enough
 *  to `--color-success` (`#4db251`) that a paused device read as a working one
 *  in the list, which is the one thing the separate state existed to prevent.
 *  Correcting the token is a theme-wide change (the paused banner, the live
 *  test panel and two dashboard cards all take it) and is not made here.
 *
 *  `orphaned` already used amber for the same "not a fault, but not running"
 *  band, and it stays distinguishable because it is the only one drawn as a
 *  triangle rather than a dot. */
const ATTENTION = "var(--color-warning, #f59e0b)";

export function DeviceStatusDot({
  connected, orphaned, paused, restarting, size = 10,
}: DeviceStatusDotProps) {
  const color = orphaned
    ? ATTENTION
    : restarting
      ? ATTENTION
      : paused
        ? ATTENTION
        : connected
          ? "var(--color-success)"
          : "var(--color-error)";
  const title = orphaned
    ? "Driver not installed"
    : restarting
      ? "Restarting"
      : paused
        ? "Paused for driver testing"
        : connected
          ? "Connected"
          : "Disconnected";
  const shape = orphaned
    ? {
        width: 0,
        height: 0,
        borderLeft: `${size / 2}px solid transparent`,
        borderRight: `${size / 2}px solid transparent`,
        borderBottom: `${size}px solid ${color}`,
        borderRadius: 0,
        backgroundColor: "transparent",
      }
    : {
        width: size,
        height: size,
        borderRadius: "50%",
        backgroundColor: color,
      };

  return (
    <span
      style={{
        display: "inline-block",
        flexShrink: 0,
        ...shape,
      }}
      title={title}
    />
  );
}
