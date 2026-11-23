import type { CommandRun } from "../../api/commandFileClient";
import type { SerialPortInfo } from "../../api/systemClient";
import { portsAlike } from "./serialPortPick";

/** Under a running file command: a change to the device waits for it. */
export function ChangeWaitingNote({ run, deviceName }: { run: CommandRun; deviceName: string }) {
  if (!run.change_waiting) return null;
  return (
    <div style={{ marginTop: 4, color: "var(--text-secondary)" }}>
      {`Your change to ${deviceName} is saved. It takes effect when ${run.label} finishes.`}
    </div>
  );
}

/** Under the serial port picker: the picked adapter's serial number is not its own. */
export function PortsAlikeNote({ ports, device }: { ports: SerialPortInfo[]; device: string }) {
  const alike = portsAlike(ports, device);
  if (alike.length === 0) return null;
  return (
    <div style={{ fontSize: 11, color: "var(--color-warning)", marginTop: "var(--space-xs)" }}>
      {`This adapter reports the same serial number as ${alike.join(", ")}, so OpenAVC tells them apart by port. If you move its cable to another USB socket, choose the port again.`}
    </div>
  );
}
