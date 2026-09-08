import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Login } from "./Login";

/**
 * A restart ends every session, and the first thing it usually costs is the
 * autosave carrying the user's work. The sign-in screen is where they land,
 * so it is where they find out that the work is still open behind it.
 */
describe("Login", () => {
  it("says nothing extra on a first sign-in", () => {
    render(<Login onSuccess={() => {}} />);

    expect(screen.getByText("Sign in to continue")).toBeInTheDocument();
    expect(screen.queryByText(/session ended/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/unsaved changes/i)).not.toBeInTheDocument();
  });

  it("says the session ended when it ended under the user", () => {
    render(<Login onSuccess={() => {}} expired />);

    expect(screen.getByText("Your session ended. Sign in to continue.")).toBeInTheDocument();
    expect(screen.queryByText(/unsaved changes/i)).not.toBeInTheDocument();
  });

  it("says the unsaved work is still there when there is some", () => {
    render(<Login onSuccess={() => {}} expired unsavedWork />);

    expect(screen.getByText("Your session ended. Sign in to continue.")).toBeInTheDocument();
    expect(
      screen.getByText("Your unsaved changes are still open in this tab."),
    ).toBeInTheDocument();
  });
});
