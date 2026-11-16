import { describe, it, expect } from "vitest";
import { lineOptions } from "./serialLine";

// What a serial line-setting picker offers and selects. The reading itself is
// held to the server's over one corpus (tests/test_serial_line_settings.py).

describe("lineOptions", () => {
  it("selects a value however it was written", () => {
    expect(lineOptions("parity", "Even").selected).toBe("E");
    expect(lineOptions("parity", "None").selected).toBe("N");
    expect(lineOptions("stopbits", 1.0).selected).toBe("1");
    expect(lineOptions("flow_control", "RTS/CTS").selected).toBe("hardware");
  });

  it("offers every choice the table holds", () => {
    expect(lineOptions("parity", "N").options.map((o) => o.label)).toEqual([
      "None", "Even", "Odd", "Mark", "Space",
    ]);
    expect(lineOptions("flow_control", undefined).options.map((o) => o.value)).toEqual([
      "none", "hardware", "software",
    ]);
  });

  it("selects the table's default when nothing is written", () => {
    expect(lineOptions("baudrate", "").selected).toBe("9600");
    expect(lineOptions("parity", undefined).selected).toBe("N");
  });

  it("adds a baud rate the list does not offer, in order", () => {
    const { selected, options } = lineOptions("baudrate", "14400");
    expect(selected).toBe("14400");
    const values = options.map((o) => Number(o.value));
    expect(values).toContain(14400);
    expect(values).toEqual([...values].sort((a, b) => a - b));
  });

  it("keeps a value with no reading as an option of its own, marked", () => {
    const { selected, options } = lineOptions("parity", "X");
    expect(selected).toBe("X");
    expect(options.at(-1)).toEqual({ value: "X", label: "X (choose another)" });
  });
});
