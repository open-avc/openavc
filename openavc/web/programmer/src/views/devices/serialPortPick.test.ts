import { describe, expect, it } from "vitest";
import type { SerialPortInfo } from "../../api/systemClient";
import { portsAlike, usbSerialToSave } from "./serialPortPick";

function port(device: string, serial_number: string): SerialPortInfo {
  return {
    device, description: device, manufacturer: "Acme", vid: 0x0403, pid: 0x6001, serial_number, hwid: "",
    usb: true, label: device,
  };
}

describe("serialPortPick", () => {
  it("binds a device to its adapter's serial number", () => {
    const ports = [port("/dev/ttyUSB0", "AB12CD"), port("/dev/ttyUSB1", "ZZ99")];
    expect(usbSerialToSave(ports, "/dev/ttyUSB0")).toBe("AB12CD");
    expect(portsAlike(ports, "/dev/ttyUSB0")).toEqual([]);
  });

  it("binds no number when another adapter reports the same one", () => {
    const ports = [port("/dev/ttyUSB0", "AB12CD"), port("/dev/ttyUSB1", "AB12CD")];
    expect(usbSerialToSave(ports, "/dev/ttyUSB1")).toBe("");
    expect(portsAlike(ports, "/dev/ttyUSB1")).toEqual(["/dev/ttyUSB0"]);
  });

  it("binds no number for a port with none or one not detected", () => {
    const ports = [port("/dev/ttyS0", ""), port("/dev/ttyS1", "")];
    expect(usbSerialToSave(ports, "/dev/ttyS0")).toBe("");
    expect(portsAlike(ports, "/dev/ttyS0")).toEqual([]);
    expect(usbSerialToSave(ports, "COM9")).toBe("");
  });
});
