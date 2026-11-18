import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";

vi.mock("../../api/restClient", () => ({ savePythonDriverSource: vi.fn() }));
vi.mock("../../store/toastStore", () => ({ showError: vi.fn() }));

import * as api from "../../api/restClient";
import { ApiError } from "../../api/errors";
import { showError } from "../../store/toastStore";
import {
  ChoiceOffered,
  SourceConflictDialog,
  reportSaveFailure,
  revisionOf,
  useSourceRevision,
} from "./sourceRevision";

const CHANGED = "This driver was changed by another session since you opened it.";

function changedElsewhere(revision: string): ApiError {
  return new ApiError(409, JSON.stringify({ detail: CHANGED, code: "driver_changed", revision }));
}

/** The hook with acme_widget opened at r1, and the save refused by `refusal`
 *  (none: answered "saved" at r2). */
async function savedOnce(refusal?: unknown, requireValidSyntax = false) {
  const hook = renderHook(() => useSourceRevision());
  act(() => hook.result.current.opened("r1"));
  if (refusal) vi.mocked(api.savePythonDriverSource).mockRejectedValueOnce(refusal);
  else vi.mocked(api.savePythonDriverSource).mockResolvedValueOnce({ status: "saved", revision: "r2" });
  let thrown: unknown = null;
  await act(async () => {
    try {
      await hook.result.current.save("acme_widget", "source", requireValidSyntax);
    } catch (e) {
      thrown = e;
    }
  });
  return { hook, thrown };
}

beforeEach(() => vi.clearAllMocks());

describe("The Code view's save names the copy it was opened at", () => {
  it("sends the revision the driver was opened at, then names what that save stored", async () => {
    const { hook } = await savedOnce();

    expect(api.savePythonDriverSource).toHaveBeenCalledWith("acme_widget", "source", false, "r1");
    expect(hook.result.current.readAt.current).toBe("r2");
  });

  it("names no revision for a file closed or opened with none", () => {
    const hook = renderHook(() => useSourceRevision());

    act(() => hook.result.current.opened("r1"));
    act(() => hook.result.current.opened(null));

    expect(hook.result.current.readAt.current).toBeNull();
    expect(revisionOf({ source: "" })).toBeNull();
    expect(revisionOf({ source: "", revision: "r1" })).toBe("r1");
  });

  it("offers the choice for a save from an older copy, naming which button it was", async () => {
    const { hook, thrown } = await savedOnce(changedElsewhere("r2"), true);

    expect(thrown).toBeInstanceOf(ChoiceOffered);
    expect(hook.result.current.conflict).toEqual(
      { message: CHANGED, revision: "r2", driverId: "acme_widget", reload: true },
    );
    expect(hook.result.current.readAt.current).toBe("r1");
  });

  it("throws any other failure as it came", async () => {
    const notFound = new ApiError(404, JSON.stringify({ detail: "Not found" }));
    const { hook, thrown } = await savedOnce(notFound);

    expect(thrown).toBe(notFound);
    expect(hook.result.current.conflict).toBeNull();
  });

  it("saves mine over it naming the revision saved now", async () => {
    const { hook } = await savedOnce(changedElsewhere("r2"));
    const retry = vi.fn(() => expect(hook.result.current.readAt.current).toBe("r2"));

    act(() => hook.result.current.saveMine(retry));

    expect(retry).toHaveBeenCalledTimes(1);
    expect(hook.result.current.conflict).toBeNull();
  });

  it("keeps editing at the revision it was opened at", async () => {
    const { hook } = await savedOnce(changedElsewhere("r2"));

    act(() => hook.result.current.dismiss());

    expect(hook.result.current.conflict).toBeNull();
    expect(hook.result.current.readAt.current).toBe("r1");
  });
});

describe("A refused Code view save, as the view says it", () => {
  it("shows the failure, unless the choice was offered for it", () => {
    reportSaveFailure("driver", new ChoiceOffered());
    expect(showError).not.toHaveBeenCalled();

    vi.spyOn(console, "error").mockImplementation(() => {});
    reportSaveFailure("driver", new ApiError(404, JSON.stringify({ detail: "Not found" })));
    expect(showError).toHaveBeenCalledWith("Save failed: Not found");
  });

  it("offers reload theirs, save mine over it with the refused button, and keep editing", async () => {
    const { hook } = await savedOnce(changedElsewhere("r2"), true);
    const onReopen = vi.fn();
    const onSave = vi.fn();
    const onSaveAndReload = vi.fn();
    const dialog = () => render(
      <SourceConflictDialog
        revision={hook.result.current}
        onReopen={onReopen}
        onSave={onSave}
        onSaveAndReload={onSaveAndReload}
      />,
    );

    dialog();
    expect(screen.getByRole("alertdialog", { name: "Changed in Another Session" })).toHaveTextContent(CHANGED);
    fireEvent.click(screen.getByRole("button", { name: "Reload Theirs" }));
    expect(onReopen).toHaveBeenCalledWith("acme_widget");
    act(() => { fireEvent.click(screen.getByRole("button", { name: "Save Mine Over It" })); });
    expect(onSaveAndReload).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("shows nothing when no save was refused", () => {
    const hook = renderHook(() => useSourceRevision());

    const { container } = render(
      <SourceConflictDialog revision={hook.result.current} onReopen={vi.fn()} onSave={vi.fn()} onSaveAndReload={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
