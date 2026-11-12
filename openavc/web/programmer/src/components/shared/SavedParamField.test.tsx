import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  saveParamValue: vi.fn(),
  savedParamState: vi.fn(),
}));
vi.mock("../../api/restClient", () => api);

import { ParamInput } from "./ParamInput";
import { validateParam } from "./paramValidation";
import { isSavedParam, savedParamName } from "./savedParams";

const KEY = "live-Acme-KEY-7f3a";
const SECRET = { type: "string", secret: true, label: "Stream Key" } as const;

function marker(name: string): string {
  return `<saved on this system #${name}>`;
}

beforeEach(() => {
  api.saveParamValue.mockReset();
  api.savedParamState.mockReset();
});

describe("a secret parameter in a macro step or a control's action", () => {
  it("keeps what is typed in its own box and hands the step only the marker", async () => {
    const saved = marker("0000000000000001");
    api.saveParamValue.mockResolvedValue(saved);
    const onChange = vi.fn();
    render(<ParamInput def={SECRET} value="" onChange={onChange} savedParamKey="key" />);

    fireEvent.change(screen.getByLabelText("Stream Key"), { target: { value: KEY } });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save Stream Key" }));

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(saved));
    expect(api.saveParamValue).toHaveBeenCalledWith(KEY);
    expect(onChange.mock.calls.flat()).not.toContain(KEY);
  });

  it("reads Saved on this system with Change once saved", async () => {
    api.savedParamState.mockResolvedValue(true);
    render(
      <ParamInput def={SECRET} value={marker("0000000000000002")} onChange={vi.fn()} savedParamKey="key" />,
    );
    expect(await screen.findByText("Saved on this system")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Change Stream Key" })).toBeTruthy();
    expect(api.savedParamState).toHaveBeenCalledWith("0000000000000002");
  });

  it("asks for it when the project says saved and this system has none", async () => {
    api.savedParamState.mockResolvedValue(false);
    render(
      <ParamInput def={SECRET} value={marker("0000000000000003")} onChange={vi.fn()} savedParamKey="key" />,
    );
    expect(await screen.findByText("Not saved on this system. Enter it here.")).toBeTruthy();
  });

  it("saves what was typed when the box is left, but not for its own Cancel", async () => {
    const saved = marker("0000000000000004");
    api.saveParamValue.mockResolvedValue(saved);
    const onChange = vi.fn();
    render(<ParamInput def={SECRET} value="" onChange={onChange} savedParamKey="key" />);
    const box = screen.getByLabelText("Stream Key");
    fireEvent.change(box, { target: { value: KEY } });
    fireEvent.blur(box, { relatedTarget: document.body });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(saved));
    expect(api.saveParamValue).toHaveBeenCalledTimes(1);
  });

  it("treats a parameter named password as a secret", () => {
    render(<ParamInput def={{ type: "string" }} value="" onChange={vi.fn()} savedParamKey="password" />);
    expect(screen.getByRole("button", { name: "Save Password" })).toBeTruthy();
  });

  it("keeps the plain masked box where nothing is saved (Send Command)", () => {
    const onChange = vi.fn();
    render(<ParamInput def={SECRET} value="" onChange={onChange} name="key" />);
    expect(screen.queryByRole("button", { name: "Save Stream Key" })).toBeNull();
    const box = document.querySelector("input[type=password]") as HTMLInputElement;
    fireEvent.change(box, { target: { value: KEY } });
    expect(onChange).toHaveBeenCalledWith(KEY);
  });
});

describe("the saved parameter marker", () => {
  it("is read by its exact shape", () => {
    expect(savedParamName(marker("abcdef0123456789"))).toBe("abcdef0123456789");
    expect(isSavedParam("<saved on this system>")).toBe(false);
    expect(isSavedParam(`${marker("abcdef0123456789")} `)).toBe(false);
    expect(isSavedParam(marker("ABCDEF0123456789"))).toBe(false);
  });

  it("passes the parameter's own checks", () => {
    expect(validateParam({ type: "string", pattern: "^\\d{4}$" }, marker("abcdef0123456789"))).toBeNull();
    expect(validateParam({ type: "string", pattern: "^\\d{4}$" }, "12")).not.toBeNull();
  });
});
