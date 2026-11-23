import type { SerialPortInfo } from "../../api/systemClient";

/**
 * The other detected ports whose adapter reports the same USB serial number
 * as the one at `device`. Clone USB-serial adapters can ship one fixed
 * number, so the number cannot tell them apart.
 */
export function portsAlike(ports: SerialPortInfo[], device: string): string[] {
  const serial = ports.find((p) => p.device === device)?.serial_number;
  if (!serial) return [];
  return ports.filter((p) => p.device !== device && p.serial_number === serial).map((p) => p.device);
}

/**
 * The USB serial number a device picked at `device` is bound to: the
 * adapter's, so the device follows its cable to a new port name, or none
 * when another attached adapter reports the same number, so the device stays
 * on the port it was set to (the server dials the stored port then too,
 * transport/serial_transport.py `require_usb_adapter`).
 */
export function usbSerialToSave(ports: SerialPortInfo[], device: string): string {
  if (portsAlike(ports, device).length > 0) return "";
  return ports.find((p) => p.device === device)?.serial_number ?? "";
}
