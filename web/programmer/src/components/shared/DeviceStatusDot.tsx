interface DeviceStatusDotProps {
  connected: boolean;
  orphaned?: boolean;
  size?: number;
}

export function DeviceStatusDot({ connected, orphaned, size = 10 }: DeviceStatusDotProps) {
  const color = orphaned
    ? "var(--color-warning, #f59e0b)"
    : connected
      ? "var(--color-success)"
      : "var(--color-error)";
  const title = orphaned
    ? "Driver not installed"
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
