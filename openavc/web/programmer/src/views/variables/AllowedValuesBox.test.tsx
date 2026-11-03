import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AllowedValuesBox } from "./VariableEditor";

function Harness({ changed }: { changed: (allowed: string[] | null) => void }) {
  const [allowed, setAllowed] = useState<string[] | null>(null);
  return <AllowedValuesBox allowed={allowed} style={{}} onChange={(next) => {
    changed(next);
    setAllowed(next);
  }} />;
}

describe("allowed values, one per line", () => {
  it("keeps the line Enter opens, so a second value can be typed", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    render(<Harness changed={changed} />);
    const box = screen.getByLabelText("Allowed values");
    await user.type(box, "everyday{Enter}christmas{Enter}easter");
    expect(box).toHaveValue("everyday\nchristmas\neaster");
    expect(changed).toHaveBeenLastCalledWith(["everyday", "christmas", "easter"]);
  });

  it("saves nothing for an empty box", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    render(<Harness changed={changed} />);
    const box = screen.getByLabelText("Allowed values");
    await user.type(box, "a{Backspace}");
    expect(changed).toHaveBeenLastCalledWith(null);
  });
});
