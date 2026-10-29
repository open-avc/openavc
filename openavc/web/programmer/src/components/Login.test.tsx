import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Login, RESET_GUIDE_URL } from "./Login";

const loginWithPassword = vi.fn();
vi.mock("../api/auth", () => ({
  loginWithPassword: (...args: unknown[]) => loginWithPassword(...args),
}));

async function signInAs(user: string) {
  loginWithPassword.mockResolvedValueOnce({ ok: false, status: 401 });
  fireEvent.change(screen.getByLabelText(/username/i), { target: { value: user } });
  fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "not it" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign In" }));
  return screen.findByText(/^Wrong username or password/);
}

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

  it("does not guess the username", () => {
    // It used to open pre-filled with "admin", which is only ever right for
    // the default. A controller claimed with its own username then answered
    // a correct password with "Wrong username or password." and nothing
    // pointing at the name. The client cannot learn it, so the field is empty
    // and it takes the focus.
    render(<Login onSuccess={() => {}} />);

    const user = screen.getByLabelText(/username/i) as HTMLInputElement;
    expect(user.value).toBe("");
    expect(document.activeElement).toBe(user);
  });

  it("says the unsaved work is still there when there is some", () => {
    render(<Login onSuccess={() => {}} expired unsavedWork />);

    expect(screen.getByText("Your session ended. Sign in to continue.")).toBeInTheDocument();
    expect(
      screen.getByText("Your unsaved changes are still open in this tab."),
    ).toBeInTheDocument();
  });

  it("names the cloud account only when an email was typed as the username", async () => {
    render(<Login onSuccess={() => {}} />);
    expect((await signInAs("someone@example.com")).textContent).toBe(
      "Wrong username or password. This is the Programmer's own login. Your OpenAVC Cloud account doesn't work here.",
    );
  });

  it("says nothing about the cloud for an ordinary username", async () => {
    render(<Login onSuccess={() => {}} />);
    expect((await signInAs("admin")).textContent).toBe("Wrong username or password.");
  });

  it("asks for the username when it was left blank", async () => {
    render(<Login onSuccess={() => {}} />);
    expect((await signInAs("")).textContent).toBe(
      "Wrong username or password. If a username was set up, enter it too.",
    );
  });

  it("shows how to reset a forgotten password on each kind of install", () => {
    render(<Login onSuccess={() => {}} />);
    expect(screen.queryByText(/On the computer running OpenAVC/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Forgot your password?" }));

    expect(screen.getByText(/On the computer running OpenAVC/)).toBeInTheDocument();
    expect(screen.getAllByText(/choose Reset Programmer Password/)).toHaveLength(2);
    expect(screen.getByRole("link", { name: "follow the steps in the guide" })).toHaveAttribute(
      "href",
      RESET_GUIDE_URL,
    );
  });
});
