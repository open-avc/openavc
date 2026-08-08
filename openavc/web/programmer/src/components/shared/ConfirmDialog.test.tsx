import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
  it("renders only Cancel and Confirm by default", () => {
    render(
      <ConfirmDialog title="T" message="M" onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("renders the extra action and fires its handler", async () => {
    const onExtra = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        title="Theme already exists"
        message="M"
        confirmLabel="Overwrite"
        extraActionLabel="Keep both"
        onExtraAction={onExtra}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    expect(screen.getAllByRole("button")).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Keep both" }));
    expect(onExtra).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
