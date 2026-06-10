interface DeviceStatusDotProps {
  connected: boolean;
  orphaned?: boolean;
  /** Paused for driver testing (device.<id>.paused) — auto-reconnect is
   *  suspended, so show a distinct state instead of a plain "offline". */
  paused?: boolean;
  size?: number;
}

export function DeviceStatusDot({ connected, orphaned, paused, size = 10 }: DeviceStatusDotProps) {
  const color = orphaned
    ? "var(--color-warning, #f59e0b)"
    : paused
      ? "var(--color-info, #6aa3d6)"
      : connected
        ? "var(--color-success)"
        : "var(--color-error)";
  const title = orphaned
    ? "Driver not installed"
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
