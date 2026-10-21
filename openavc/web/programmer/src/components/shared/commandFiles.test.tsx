import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { CommandParamForm } from "./CommandParamForm";
import {
  describeSize,
  fileParamsBlocked,
  fileRefusal,
  isFileCommand,
} from "./commandFiles";
import { coerceCommandParams, commandParamsBlocked } from "./commandParams";
import { commandOptions } from "./pickerOptions";

const IMAGE = { type: "file", label: "Firmware", required: true, accept: [".bin"], max_bytes: 50_000_000 };
const PARAMS = { image: IMAGE, slot: { type: "integer", label: "Slot" } } as const;
const COMMANDS = {
  load_firmware: { label: "Load Firmware", params: PARAMS },
  backup: { label: "Back Up Settings", returns_file: true },
  power_on: { label: "Power On" },
};

function file(name: string, size: number): File {
  const f = new File(["x"], name);
  Object.defineProperty(f, "size", { value: size });
  return f;
}

describe("a command that takes or hands back a file", () => {
  it("is offered only where a file can be given", () => {
    expect(isFileCommand(COMMANDS.load_firmware)).toBe(true);
    expect(isFileCommand(COMMANDS.backup)).toBe(true);
    expect(isFileCommand(COMMANDS.power_on)).toBe(false);
    // A macro step, a panel binding, a plugin's command picker.
    expect(commandOptions(COMMANDS).map((o) => o.value)).toEqual(["power_on"]);
    // Send Command on the device page, a device audit.
    expect(commandOptions(COMMANDS, { fileCommands: true }).map((o) => o.value)).toEqual([
      "load_firmware", "backup", "power_on",
    ]);
  });

  it("refuses a file in the server's own words", () => {
    expect(fileRefusal("image", IMAGE, file("fw.bin", 60_000_000))).toBe(
      "fw.bin is 60 MB. Firmware takes a file up to 50 MB.",
    );
    expect(fileRefusal("image", IMAGE, file("fw.zip", 10))).toBe("Firmware takes a .bin file.");
    expect(fileRefusal("image", IMAGE, file("FW.BIN", 10))).toBeNull();
    expect(fileRefusal("image", IMAGE, file("fw.bin", 0))).toBe("fw.bin is empty. Choose the file again.");
    expect(describeSize(940)).toBe("940 bytes");
    expect(describeSize(3_412_000)).toBe("3.4 MB");
  });

  it("holds Send until a required file is chosen and will do", () => {
    expect(fileParamsBlocked(PARAMS, {})).toBe(true);
    expect(fileParamsBlocked(PARAMS, { image: file("fw.zip", 10) })).toBe(true);
    expect(fileParamsBlocked(PARAMS, { image: file("fw.bin", 10) })).toBe(false);
    // The form holds the file's name, so the required check reads it too, and
    // the name is never sent as the value.
    expect(commandParamsBlocked(PARAMS, { image: "fw.bin", slot: "" })).toBe(false);
    expect(coerceCommandParams(PARAMS, { image: "fw.bin", slot: "2" })).toEqual({ slot: 2 });
  });

  it("draws a file picker that shows the chosen file and why it will not do", () => {
    const chosen = vi.fn();
    function Form() {
      const [values, setValues] = useState<Record<string, string>>({ image: "", slot: "" });
      const [files, setFiles] = useState<Record<string, File | null>>({});
      return (
        <CommandParamForm
          params={PARAMS}
          values={values}
          onChange={(name, v) => setValues((p) => ({ ...p, [name]: v }))}
          files={files}
          onFileChange={(name, f) => {
            chosen(name, f?.name, values);
            setFiles((p) => ({ ...p, [name]: f }));
          }}
        />
      );
    }
    render(<Form />);
    expect(screen.getByText("No file chosen")).toBeTruthy();
    const input = screen.getByTestId("file-param-image") as HTMLInputElement;
    expect(input.accept).toBe(".bin");
    fireEvent.change(input, { target: { files: [file("notes.txt", 12)] } });
    expect(chosen).toHaveBeenCalledWith("image", "notes.txt", expect.anything());
    expect(screen.getByText("notes.txt, 12 bytes")).toBeTruthy();
    expect(screen.getByText("Firmware takes a .bin file.")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Clear notes.txt"));
    expect(screen.getByText("No file chosen")).toBeTruthy();
  });

  it("says where the file is chosen on a surface that cannot send one", () => {
    render(<CommandParamForm params={PARAMS} values={{}} onChange={() => {}} />);
    expect(
      screen.getByText("The file is chosen when this command is sent from the device's page."),
    ).toBeTruthy();
  });
});
