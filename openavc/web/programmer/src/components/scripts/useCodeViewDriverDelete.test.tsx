import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ProjectConfig, PythonDriverInfo } from "../../api/types";

vi.mock("../../api/restClient", () => ({ deletePythonDriver: vi.fn() }));

import * as api from "../../api/restClient";
import { ApiError } from "../../api/errors";
import { useNavigationStore } from "../../store/navigationStore";
import { useProjectStore } from "../../store/projectStore";
import { useCodeViewDriverDelete, type CodeViewQuestion } from "./useCodeViewDriverDelete";

const DRIVERS = [{ id: "acme_widget", name: "Acme Widget" }] as PythonDriverInfo[];

function CodeView({ isDirty, ask, onDeleted }: {
  isDirty: boolean;
  ask: (question: CodeViewQuestion | null) => void;
  onDeleted: (id: string) => Promise<void>;
}) {
  const driverDelete = useCodeViewDriverDelete({ pythonDrivers: DRIVERS, isDirty, ask, onDeleted });
  return (
    <>
      <button onClick={() => driverDelete.request("acme_widget")}>Delete driver</button>
      {driverDelete.dialog}
    </>
  );
}

function project(devices: { id: string; name: string; driver: string }[]) {
  useProjectStore.setState({ project: { devices } as unknown as ProjectConfig });
}

beforeEach(() => {
  vi.clearAllMocks();
  useNavigationStore.setState({ activeView: "scripts", pendingFocus: null });
});

describe("The Code view's delete of a Python driver", () => {
  it("names the devices that use it instead of asking to delete", () => {
    project([{ id: "room_a", name: "Room A Projector", driver: "acme_widget" }]);
    const ask = vi.fn();
    render(<CodeView isDirty={false} ask={ask} onDeleted={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Delete driver" }));

    expect(screen.getByRole("alertdialog", { name: "Driver In Use" })).toHaveTextContent("Room A Projector");
    expect(ask).not.toHaveBeenCalled();
    expect(api.deletePythonDriver).not.toHaveBeenCalled();
  });

  it("asks before opening a device over unsaved changes, and opens it once answered", () => {
    project([{ id: "room_a", name: "Room A Projector", driver: "acme_widget" }]);
    const ask = vi.fn();
    render(<CodeView isDirty ask={ask} onDeleted={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete driver" }));

    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Open Device" }));

    expect(useNavigationStore.getState().activeView).toBe("scripts");
    const question: CodeViewQuestion = ask.mock.calls[0][0];
    expect(question.title).toBe("Unsaved Changes");
    question.onConfirm();
    expect(useNavigationStore.getState().pendingFocus).toEqual({ type: "device", id: "room_a" });
  });

  it("names the devices the server refused it for", async () => {
    project([]);
    vi.mocked(api.deletePythonDriver).mockRejectedValueOnce(new ApiError(409, JSON.stringify({
      detail: "Lobby Display uses this driver. Open the device and choose another driver first.",
      code: "driver_in_use",
      devices: [{ id: "lobby_1", name: "Lobby Display" }],
    })));
    const ask = vi.fn();
    const onDeleted = vi.fn();
    render(<CodeView isDirty={false} ask={ask} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete driver" }));

    (ask.mock.calls[0][0] as CodeViewQuestion).onConfirm();

    expect(await screen.findByRole("alertdialog", { name: "Driver In Use" })).toHaveTextContent("Lobby Display");
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
