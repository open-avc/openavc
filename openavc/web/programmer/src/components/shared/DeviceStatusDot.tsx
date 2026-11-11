import { pausedTitle } from "./pausedText";
import { firstAway, type AwayFirst } from "./deviceAwayOrder";

interface DeviceStatusDotProps {
  connected: boolean;
  orphaned?: boolean;
  /** Paused (device.<id>.paused) by Live Test or a Device Audit —
   *  auto-reconnect is suspended, so show a distinct state instead of a plain
   *  "offline". */
  paused?: boolean;
  /** Who holds the pause (device.<id>.paused_for), named in the tooltip. */
  pausedFor?: unknown;
  /** Away because a command restarted it (device.<id>.restarting) — absence
   *  we asked for, coming back on its own, so not the red a fault gets. */
  restarting?: boolean;
  /** Off the network because a command put it to sleep (device.<id>.asleep).
   *  Waiting for its wake command, not a fault, so not the red either. */
  asleep?: boolean;
  size?: number;
}

/** The rule this dot keeps: GREEN means you can drive this device right now.
 *  Anything else is amber, grey or red, and the tooltip (plus the card's own
 *  banner) says which of the five it is.
 *
 *  Asleep takes the muted text colour: the device was put to sleep on purpose
 *  and stays that way until its wake command, so it is neither the red of a
 *  fault nor the amber of something that will come back by itself.
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

const ASLEEP = "var(--text-muted)";

/** Each way of being away: its colour and what the tooltip says. Which one
 *  wins is the device page's order (`firstAway`). */
const AWAY_LOOK: Record<AwayFirst, { color: string; title: (pausedFor: unknown) => string }> = {
  orphaned: { color: ATTENTION, title: () => "Driver not installed" },
  paused: { color: ATTENTION, title: pausedTitle },
  restarting: { color: ATTENTION, title: () => "Restarting" },
  asleep: { color: ASLEEP, title: () => "Asleep" },
};

/** The dot's colour and tooltip. */
function dotLook(props: DeviceStatusDotProps): { color: string; title: string } {
  const away = firstAway(props);
  if (away) return { color: AWAY_LOOK[away].color, title: AWAY_LOOK[away].title(props.pausedFor) };
  return props.connected
    ? { color: "var(--color-success)", title: "Connected" }
    : { color: "var(--color-error)", title: "Disconnected" };
}

export function DeviceStatusDot(props: DeviceStatusDotProps) {
  const { orphaned, size = 10 } = props;
  const { color, title } = dotLook(props);
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
