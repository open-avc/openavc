/**
 * What this picker owes the `<select>` it replaced.
 *
 * The one that matters most is clearing. A `<select value="">Select device...`
 * option is a real, re-selectable option, so every screen this picker landed on
 * could un-set a field by choosing it again. A picker that only ever writes a
 * value would have taken that away silently on twenty-odd fields at once, and
 * nothing else in the suite would have noticed.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SearchableSelect } from "./SearchableSelect";
import { Modal } from "./Modal";

const OPTIONS = [
  { value: "proj1", label: "Main Projector", hint: "proj1", meta: "pjlink_class1" },
  { value: "dsp1", label: "Room DSP", hint: "dsp1", meta: "biamp_tesira" },
  { value: "cam1", label: "Camera", hint: "cam1", keywords: "visca ptz" },
];

function open(triggerName: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name: triggerName }));
}

describe("SearchableSelect", () => {
  it("shows the placeholder while nothing is chosen, and the label once it is", () => {
    const { rerender } = render(
      <SearchableSelect value="" onChange={vi.fn()} options={OPTIONS} placeholder="Select device..." />,
    );
    expect(screen.getByRole("button", { name: "Select device..." })).toBeTruthy();

    rerender(
      <SearchableSelect value="dsp1" onChange={vi.fn()} options={OPTIONS} placeholder="Select device..." />,
    );
    expect(screen.getByRole("button", { name: "Room DSP" })).toBeTruthy();
  });

  it("offers the empty choice as a row, so a field can be cleared again", () => {
    const onChange = vi.fn();
    render(
      <SearchableSelect value="dsp1" onChange={onChange} options={OPTIONS} placeholder="Select device..." />,
    );
    open("Room DSP");
    fireEvent.click(screen.getByRole("option", { name: "Select device..." }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("leaves the empty choice out when the list never had one", () => {
    render(
      <SearchableSelect
        value=""
        onChange={vi.fn()}
        options={OPTIONS}
        allowEmpty={false}
        placeholder="Select device..."
      />,
    );
    open("Select device...");
    expect(screen.queryByRole("option", { name: "Select device..." })).toBeNull();
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });

  it("searches the id and the undrawn keywords, not just the label", () => {
    render(<SearchableSelect value="" onChange={vi.fn()} options={OPTIONS} placeholder="Pick..." />);
    open("Pick...");
    const search = screen.getByPlaceholderText("Search...");

    // by id
    fireEvent.change(search, { target: { value: "dsp1" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      expect.stringContaining("Room DSP"),
    ]);

    // by a word nobody put in the name
    fireEvent.change(search, { target: { value: "ptz" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      expect.stringContaining("Camera"),
    ]);
  });

  it("says so when nothing matches, rather than drawing an empty box", () => {
    render(<SearchableSelect value="" onChange={vi.fn()} options={OPTIONS} placeholder="Pick..." />);
    open("Pick...");
    fireEvent.change(screen.getByPlaceholderText("Search..."), { target: { value: "zzz" } });
    expect(screen.getByText(/Nothing matching/)).toBeTruthy();
  });

  it("draws a value the list does not contain, instead of reading as empty", () => {
    // A binding pointed at a deleted macro. A <select> renders this blank,
    // which is indistinguishable from nothing chosen.
    render(
      <SearchableSelect value="deleted_macro" onChange={vi.fn()} options={OPTIONS} placeholder="Select..." />,
    );
    expect(screen.getByRole("button", { name: "deleted_macro" })).toBeTruthy();
  });

  it("picks with the keyboard: arrows move, Enter chooses", () => {
    const onChange = vi.fn();
    render(
      <SearchableSelect value="" onChange={onChange} options={OPTIONS} allowEmpty={false} placeholder="Pick..." />,
    );
    open("Pick...");
    const search = screen.getByPlaceholderText("Search...");
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("dsp1");
  });

  it("groups stay separate, and an empty group is not drawn", () => {
    render(
      <SearchableSelect
        value=""
        onChange={vi.fn()}
        groups={[
          { label: "Pages", options: [{ value: "home", label: "Home" }] },
          { label: "Overlays", options: [] },
        ]}
        placeholder="Select page..."
      />,
    );
    open("Select page...");
    expect(screen.getByText("Pages")).toBeTruthy();
    expect(screen.queryByText("Overlays")).toBeNull();
  });

  it("Escape closes the list and leaves the dialog behind it open", () => {
    const onDialogClose = vi.fn();
    render(
      <Modal onClose={onDialogClose}>
        <SearchableSelect value="" onChange={vi.fn()} options={OPTIONS} placeholder="Pick..." />
      </Modal>,
    );
    open("Pick...");
    const search = screen.getByPlaceholderText("Search...");
    fireEvent.keyDown(search, { key: "Escape" });

    expect(screen.queryByPlaceholderText("Search...")).toBeNull();
    expect(onDialogClose).not.toHaveBeenCalled();
  });
});
