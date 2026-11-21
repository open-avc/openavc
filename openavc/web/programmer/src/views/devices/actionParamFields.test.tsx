import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ActionParamFields, seedParamValues } from "./actionParamFields";

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

  it("start a parameter named password blank and masked, whatever its definition says", () => {
    const params = {
      password: { type: "string", default: "admin123", label: "Password" },
      level: { type: "integer", default: 5, label: "Level" },
    };
    expect(seedParamValues(params)).toEqual({ password: "", level: "5" });
    render(
      <ActionParamFields params={params} values={{ password: "", level: "5" }} onChange={vi.fn()} />,
    );
    expect((screen.getByPlaceholderText("Password") as HTMLInputElement).type).toBe("password");
  });
});
