import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ActionParamFields } from "./actionParamFields";

// Quick Actions and the setup wizard name a field the way every command form
// does: its label, else its key made readable.
describe("an action's fields", () => {
  it("are named by their label, else their key made readable", () => {
    render(
      <ActionParamFields
        params={{
          level: { type: "integer", label: "Level" },
          input_id: { type: "string", required: true },
          delay_sec: { type: "integer", label: " " },
        }}
        values={{ level: "", input_id: "", delay_sec: "" }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText("Level")).toBeTruthy();
    expect(screen.getByText("Input ID")).toBeTruthy();
    expect(screen.getByText("Delay (sec)")).toBeTruthy();
    expect(screen.queryByText("input_id")).toBeNull();
    expect(screen.getByPlaceholderText("Input ID")).toBeTruthy();
  });
});
