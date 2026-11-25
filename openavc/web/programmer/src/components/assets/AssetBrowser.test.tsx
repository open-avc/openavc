import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../api/restClient", () => ({
  listAssets: vi.fn(() => Promise.resolve({ assets: [], total_size: 0 })),
}));

import { AssetBrowser, deleteMessage } from "./AssetBrowser";
import { listAssets } from "../../api/restClient";
import type { AssetInfo } from "../../api/systemClient";
import { useConnectionStore } from "../../store/connectionStore";

function asset(name: string, used_by?: string[]): AssetInfo {
  return { name, size: 100, type: "image", extension: "png", used_by };
}

describe("the delete dialog", () => {
  it("names what still shows the asset, so the refusal is not a surprise", () => {
    const message = deleteMessage("logo.png", [
      asset("logo.png", ["element 'hero' on page 'main'", "theme 'midnight'"]),
    ]);
    expect(message).toContain("element 'hero' on page 'main'");
    expect(message).toContain("theme 'midnight'");
    expect(message).toContain("refused");
  });

  it("warns plainly when nothing shows it", () => {
    const message = deleteMessage("spare.png", [asset("spare.png", [])]);
    expect(message).toContain("cannot be undone");
    expect(message).not.toContain("refused");
  });

  it("does not claim nothing uses an asset it has no answer about", () => {
    // `used_by` is absent rather than empty when the server had no project to
    // ask -- a different thing from "used by nothing", and saying the wrong
    // one here would talk somebody into a delete the server then refuses.
    const message = deleteMessage("mystery.png", [asset("mystery.png")]);
    expect(message).toContain("cannot be undone");
    expect(message).not.toContain("refused");
  });

  it("falls back to the plain warning for an asset it was not given", () => {
    expect(deleteMessage("gone.png", [])).toContain("cannot be undone");
  });
});

describe("the asset list", () => {
  // An asset the assistant deleted, or a project replaced with its own
  // files, changes no project data the list reloads on; the server's
  // catalog of the files (project.assets) is what moves.
  it("lists the assets again when the project's asset catalog changes", async () => {
    vi.mocked(listAssets).mockClear();
    render(<AssetBrowser filter="all" selectMode="manage" />);
    await waitFor(() => expect(vi.mocked(listAssets)).toHaveBeenCalledTimes(1));

    act(() => useConnectionStore.getState().applyStateUpdate({ "project.assets": "[]" }));
    await waitFor(() => expect(vi.mocked(listAssets)).toHaveBeenCalledTimes(2));

    act(() => useConnectionStore.getState().applyStateUpdate({ "var.other": 1 }));
    await new Promise((r) => setTimeout(r, 20));
    expect(vi.mocked(listAssets)).toHaveBeenCalledTimes(2);
  });

  it("keeps the list on screen while it is read again", async () => {
    vi.mocked(listAssets).mockClear();
    render(<AssetBrowser filter="all" selectMode="manage" />);
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    vi.mocked(listAssets).mockReturnValueOnce(new Promise(() => {}));
    act(() => useConnectionStore.getState().applyStateUpdate({ "project.assets": "[1]" }));
    await waitFor(() => expect(vi.mocked(listAssets)).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Loading...")).toBeNull();
  });
});
