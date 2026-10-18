import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WhenStateEditor } from "./WhenStateEditor";
import type { DriverDefinition } from "../../api/types";

// A state condition the author has not filled in must not hold the poll back
// unseen: a new one starts on a variable whose value it shows, and a text box
// left empty is stored empty, which a save refuses and names.

const draft = {
  id: "acme_widget",
  name: "Acme Widget",
  transport: "tcp",
  state_variables: {
    input_name: { type: "string", label: "Input Name" },
    standby: { type: "boolean", label: "Standby" },
  },
} as unknown as DriverDefinition;

describe("WhenStateEditor", () => {
  it("adds a condition on a boolean before a text variable", async () => {
    const onChange = vi.fn();
    render(<WhenStateEditor draft={draft} gate={undefined} onChange={onChange} lead="Poll only while" />);
    await userEvent.click(screen.getByRole("button", { name: /Add a state condition/ }));
    expect(onChange).toHaveBeenLastCalledWith({ standby: false });
  });

  it("stores a cleared text value as empty rather than keeping the old one", () => {
    const onChange = vi.fn();
    render(
      <WhenStateEditor draft={draft} gate={{ input_name: "HDMI" }} onChange={onChange} lead="Poll only while" />,
    );
    fireEvent.change(screen.getByLabelText("Values of input_name"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith({ input_name: "" });
  });
});
