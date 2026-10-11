import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";

// After a change of who can connect, the restart waits where the new choice
// answers: another address of this computer, or, for a browser the change
// cuts off, the new choice's own addresses until it says where to go. That is
// never the 60-second "may have refused the new configuration" failure.

vi.mock("../../api/restClient", () => ({
  restartSystem: vi.fn(async () => ({ status: "restarting", mode: "graceful", delay_seconds: 2 })),
  downloadCertificate: vi.fn(),
}));

import { RestartProgressDialog } from "./RestartProgressDialog";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.useFakeTimers();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function runFor(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const handoff = {
  lines: <span>This page will stop loading here.</span>,
  saved: <span>Saved. Open the Programmer on this computer's own screen.</span>,
};

describe("RestartProgressDialog after a change of who can connect", () => {
  it("hands over at once when the new choice has no address this browser could try", async () => {
    render(
      <RestartProgressDialog
        targetUrl="http://localhost:8080/programmer"
        alsoTry={[]}
        handoff={handoff}
        isProtocolSwitch={false}
        expectsNewCert={false}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("This page will stop loading here.")).toBeTruthy();
    await runFor(3500);
    expect(screen.getByTestId("restart-handoff-saved").textContent).toContain("Saved. Open the Programmer");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/may have refused the new configuration/)).toBeNull();
  });

  it("tries the new choice's address, then hands over instead of failing", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(
      <RestartProgressDialog
        targetUrl="http://10.20.30.5:8080/programmer"
        alsoTry={["http://10.20.30.5:8080/programmer"]}
        handoff={handoff}
        isProtocolSwitch={false}
        expectsNewCert={false}
        onClose={() => {}}
      />,
    );
    await runFor(3500 + 30_000);
    const probed = fetchMock.mock.calls.map((c) => [String(c[0]), (c[1] as RequestInit).mode]);
    expect(probed.length).toBeGreaterThan(0);
    // Only the new address, and without asking it for CORS.
    expect(new Set(probed.map((p) => p.join(" ")))).toEqual(
      new Set(["http://10.20.30.5:8080/api/health no-cors"]),
    );
    expect(screen.getByTestId("restart-handoff-saved")).toBeTruthy();
    expect(screen.queryByText(/may have refused the new configuration/)).toBeNull();
  });

  it("moves to the address the new choice answers on", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, origin: "http://127.0.0.2:8080", assign });
    fetchMock.mockResolvedValue({ ok: false, type: "opaque" });
    render(
      <RestartProgressDialog
        targetUrl="http://127.0.0.1:8080/programmer"
        isProtocolSwitch={false}
        expectsNewCert={false}
        onClose={() => {}}
      />,
    );
    await runFor(3500 + 1000);
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://127.0.0.1:8080/api/health");
    expect(assign).toHaveBeenCalledWith("http://127.0.0.1:8080/programmer");
  });
});
