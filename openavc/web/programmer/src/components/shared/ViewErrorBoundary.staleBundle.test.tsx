import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ViewErrorBoundary } from "./ViewErrorBoundary";

/**
 * An IDE tab whose bundle was rebuilt underneath it.
 *
 * The views are hashed lazy chunks, so an in-place update (or a developer's
 * rebuild) invalidates every chunk URL the open document knows. The next view
 * opened cannot be fetched and the tab showed a crash card naming a file the
 * user has never heard of; changing tabs does not recover it, because the same
 * stale document is still loaded. The only cure is a reload, and nothing said
 * so.
 */

const reload = vi.fn();
let realLocation: Location;
const realSessionStorage = window.sessionStorage;

function Boom({ message }: { message: string }): never {
  throw new Error(message);
}

const STALE = "Failed to fetch dynamically imported module: "
  + "http://localhost:8080/programmer/assets/ProjectView-Cdtc1FYj.js";

beforeEach(() => {
  reload.mockClear();
  realLocation = window.location;
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, reload },
  });
  Object.defineProperty(window, "sessionStorage", {
    configurable: true, value: realSessionStorage,
  });
  window.sessionStorage.clear();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
  Object.defineProperty(window, "sessionStorage", {
    configurable: true, value: realSessionStorage,
  });
  vi.restoreAllMocks();
});

describe("a view whose chunk is no longer on the server", () => {
  it("reloads the tab instead of showing a crash card", () => {
    render(
      <ViewErrorBoundary viewName="Project">
        <Boom message={STALE} />
      </ViewErrorBoundary>,
    );
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("This view crashed")).toBeNull();
    expect(screen.getByText(/updated/i)).toBeTruthy();
  });

  it("reloads once, not in a loop, when the reload did not help", () => {
    render(
      <ViewErrorBoundary viewName="Project">
        <Boom message={STALE} />
      </ViewErrorBoundary>,
    );
    expect(reload).toHaveBeenCalledTimes(1);

    // The mark survives the (simulated) reload, so the second crash lands on
    // the card with the button rather than reloading again.
    render(
      <ViewErrorBoundary viewName="Project">
        <Boom message={STALE} />
      </ViewErrorBoundary>,
    );
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole("button", { name: "Reload Page" }).length).toBeGreaterThan(0);
  });

  it("does not reload when there is nowhere to remember that it did", () => {
    // Storage blocked (a private window, a locked-down kiosk): an unguarded
    // reload would be a loop, so the card is the recovery instead.
    const blocked = {
      getItem: () => null,
      setItem: () => { throw new Error("The operation is insecure."); },
      removeItem: () => {}, clear: () => {}, key: () => null, length: 0,
    };
    Object.defineProperty(window, "sessionStorage", { configurable: true, value: blocked });
    render(
      <ViewErrorBoundary viewName="Project">
        <Boom message={STALE} />
      </ViewErrorBoundary>,
    );
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByText(/updated/i)).toBeTruthy();
  });

  it.each([
    "error loading dynamically imported module: /assets/MacroView-x.js",
    "Importing a module script failed.",
    "Unable to preload CSS for /assets/UIBuilderView-x.css",
    "Failed to load module script: Expected a JavaScript module script but the "
      + "server responded with a MIME type of \"text/html\".",
  ])("recognises the shape browsers use: %s", (message) => {
    render(
      <ViewErrorBoundary viewName="Project">
        <Boom message={message} />
      </ViewErrorBoundary>,
    );
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

describe("an ordinary crash in a view", () => {
  it("still shows the crash card and never reloads by itself", () => {
    render(
      <ViewErrorBoundary viewName="Project">
        <Boom message="Cannot read properties of undefined (reading 'id')" />
      </ViewErrorBoundary>,
    );
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByText("This view crashed")).toBeTruthy();
    expect(screen.getByText(/Cannot read properties of undefined/)).toBeTruthy();
  });
});
