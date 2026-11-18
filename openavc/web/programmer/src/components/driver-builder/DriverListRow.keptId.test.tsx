import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LoadNotes } from "./DriverListRow";
import { KEPT_ID_TEXT, LOADED_WITH_NOTICES_TEXT } from "./installedDriverList";

describe("A driver page for a driver kept under an ID a new driver could not be given", () => {
  it("says it keeps working under that ID, apart from any mistakes in its file", () => {
    render(<LoadNotes driverId="acme-widget" notLoaded={false} />);
    expect(screen.getByText(KEPT_ID_TEXT)).toBeTruthy();
    expect(screen.queryByText(LOADED_WITH_NOTICES_TEXT)).toBeNull();
  });

  it("says nothing of an ID a new driver may have", () => {
    render(<LoadNotes driverId="acme_widget" notLoaded={false} />);
    expect(screen.queryByText(KEPT_ID_TEXT)).toBeNull();
  });
});
