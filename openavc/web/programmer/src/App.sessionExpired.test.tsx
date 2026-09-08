/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8080/programmer/" }
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import App from "./App";
import { AUTH_REQUIRED_EVENT } from "./api/auth";
import { useProjectStore } from "./store/projectStore";

/**
 * The 401 that ends a session usually arrives on the autosave, so the sign-in
 * screen is the first thing the user sees after a restart. What it says about
 * their unsaved work is decided at the moment the session ended, not at
 * render, because nothing can change it after the app comes down.
 */

beforeEach(() => {
  window.fetch = vi.fn(async () =>
    new Response('{"state":"required"}', { status: 200 }),
  ) as typeof window.fetch;
  useProjectStore.setState({ dirty: false });
});

afterEach(() => {
  useProjectStore.setState({ dirty: false });
});

/** Render the app and let the auth probe settle on the sign-in screen. */
async function signInScreen() {
  render(<App />);
  expect(await screen.findByText("Sign in to continue")).toBeInTheDocument();
}

describe("a session that ends while the app is open", () => {
  it("tells the user, instead of showing a bare sign-in form", async () => {
    await signInScreen();

    act(() => {
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    });

    expect(screen.getByText("Your session ended. Sign in to continue.")).toBeInTheDocument();
    expect(screen.queryByText(/unsaved changes/i)).not.toBeInTheDocument();
  });

  it("says the unsaved edit survived it", async () => {
    await signInScreen();
    useProjectStore.setState({ dirty: true });

    act(() => {
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    });

    expect(
      screen.getByText("Your unsaved changes are still open in this tab."),
    ).toBeInTheDocument();
  });
});
