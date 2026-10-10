import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { useState } from "react";
import { ConfigFieldInputs } from "./DeviceDialogs";
import type { DriverInfo } from "../../api/types";

// A config field a driver declares `learned_from` is filled in from what the
// device reports once it connects, so the form says so. And a model list that
// carries its own blank entry ("Detect automatically") must not also get the
// generic "Select..." placeholder: two blank options, one of them meaningless.

const driverInfo = {
  id: "acme_widget",
  config_schema: {
    model: {
      type: "enum",
      label: "Model",
      learned_from: "model",
      values: [
        { value: "", label: "Detect automatically" },
        { value: "W100", label: "W100" },
      ],
    },
    zone: {
      type: "enum",
      label: "Zone",
      values: ["A", "B"],
    },
    mac_address: {
      type: "string",
      label: "MAC Address",
      description: "For Wake-on-LAN.",
      learned_from: "mac_address",
    },
  },
} as unknown as DriverInfo;

function Harness() {
  const [values, setValues] = useState<Record<string, string>>({ model: "" });
  return (
    <ConfigFieldInputs
      configKeys={["model", "zone", "mac_address"]}
      driverInfo={driverInfo}
      configValues={values}
      setConfigValues={setValues}
    />
  );
}

describe("ConfigFieldInputs: fields the device fills in", () => {
  it("says a learned field is filled in from the device", () => {
    render(<Harness />);
    const model = screen.getByLabelText("Model");
    expect(model).toHaveAccessibleDescription("Filled in from the device when it connects.");
    const mac = screen.getByLabelText("MAC Address");
    expect(mac).toHaveAccessibleDescription(
      "For Wake-on-LAN. Filled in from the device when it connects.",
    );
    expect(screen.getByLabelText("Zone")).not.toHaveAccessibleDescription(
      "Filled in from the device when it connects.",
    );
  });

  it("offers a listed blank entry instead of the Select placeholder", () => {
    render(<Harness />);
    const model = screen.getByLabelText("Model") as HTMLSelectElement;
    const labels = Array.from(model.options).map((o) => o.textContent);
    expect(labels).toEqual(["Detect automatically", "W100"]);
    const zone = screen.getByLabelText("Zone") as HTMLSelectElement;
    expect(Array.from(zone.options).map((o) => o.textContent)).toEqual(["Select...", "A", "B"]);
  });
});
