import { pausedTitle } from "./pausedText";
import { PRESENCE_LABEL, type PresenceWord } from "./devicePresence";

interface DeviceStatusDotProps {
  /** The device's presence word (device.<id>.presence, devicePresence.ts). */
  presence: PresenceWord;
  /** Who holds a pause (device.<id>.paused_for), named in the tooltip. */
  pausedFor?: unknown;
  size?: number;
}

/** The rule this dot keeps: GREEN means you can drive this device right now.
 *  Anything else is amber, grey or red, and the tooltip (plus the card's own
 *  banner) says which.
 *
 *  Amber is away for now and coming back by itself, or waiting on something
 *  that is not a fault: Live Test or a Device Audit holds it, a command is
 *  restarting it, or its driver is not installed (the one drawn as a
 *  triangle). Grey is away until someone brings it back: a command put it to
 *  sleep and it waits for its wake command, or the project turns it off. Red
 *  is a fault, and a device not connected yet with no fault known, which has
 *  no look of its own yet.
 *
 *  Paused used to take `--color-info` for the amber band, on the reasonable
 *  assumption that the token was a blue — its fallback is `#6aa3d6`. It is not:
 *  `tokens.css` defines it as `#8AB493`, the brand sage, which is close enough
 *  to `--color-success` (`#4db251`) that a paused device read as a working one
 *  in the list, which is the one thing the separate state existed to prevent.
 *  Correcting the token is a theme-wide change (the paused banner, the live
 *  test panel and two dashboard cards all take it) and is not made here. */
const ATTENTION = "var(--color-warning, #f59e0b)";

const AWAY = "var(--text-muted)";

const FAULT = "var(--color-error)";

/** Each word's colour. */
const COLOR: Record<PresenceWord, string> = {
  disabled: AWAY,
  waiting_for_driver: ATTENTION,
  online: "var(--color-success)",
  restarting: ATTENTION,
  asleep: AWAY,
  paused: ATTENTION,
  offline: FAULT,
  connecting: FAULT,
};

/** The dot's colour and tooltip. */
function dotLook({ presence, pausedFor }: DeviceStatusDotProps): { color: string; title: string } {
  const title = presence === "paused" ? pausedTitle(pausedFor) : PRESENCE_LABEL[presence];
  return { color: COLOR[presence], title };
}

export function DeviceStatusDot(props: DeviceStatusDotProps) {
  const { size = 10 } = props;
  const { color, title } = dotLook(props);
  const shape = props.presence === "waiting_for_driver"
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
