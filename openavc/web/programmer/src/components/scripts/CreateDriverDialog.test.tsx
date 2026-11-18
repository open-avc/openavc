import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { CreateDriverDialog } from "./CreateDriverDialog";

function fill(id: string) {
  const onSubmit = vi.fn();
  render(<CreateDriverDialog onSubmit={onSubmit} onCancel={() => {}} />);
  fireEvent.change(screen.getByPlaceholderText("e.g. lg_webos"), { target: { value: id } });
  fireEvent.change(screen.getByPlaceholderText("e.g. LG webOS Display"), { target: { value: "Acme Widget" } });
  return onSubmit;
}

describe("New Driver's ID", () => {
  it("is made an ID a new driver may have, and says so before it is created", () => {
    const onSubmit = fill("Acme-Widget 2");
    expect(screen.getByText("Will be saved as: acme_widget_2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create Driver" }));
    expect(onSubmit).toHaveBeenCalledWith("acme_widget_2", expect.stringContaining('"id": "acme_widget_2"'));
  });

  it("keeps an ID already made of lowercase letters, digits and underscores", () => {
    const onSubmit = fill("acme_widget");
    expect(screen.queryByText(/Will be saved as/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create Driver" }));
    expect(onSubmit).toHaveBeenCalledWith("acme_widget", expect.any(String));
  });
});
