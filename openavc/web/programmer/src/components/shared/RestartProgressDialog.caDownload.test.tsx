import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

// When the server comes back on HTTPS with a certificate this browser does not
// trust yet, the dialog offers the CA certificate (saved as openavc-ca.crt)
// and opens the new address in a new tab, whether or not the download worked.

const downloadCertificate = vi.fn(async () => new Blob(["CA"]));
vi.mock("../../api/restClient", () => ({
  restartSystem: vi.fn(async () => ({ status: "restarting", mode: "graceful", delay_seconds: 2 })),
  downloadCertificate: () => downloadCertificate(),
}));

import { RestartProgressDialog } from "./RestartProgressDialog";

const created = vi.fn(() => "blob:ca");
const revoked = vi.fn();
const opened = vi.fn();
const saved: string[] = [];

beforeEach(() => {
  vi.useFakeTimers();
  saved.length = 0;
  downloadCertificate.mockClear();
  created.mockClear();
  revoked.mockClear();
  opened.mockClear();
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
  vi.stubGlobal("open", opened);
  Object.assign(URL, { createObjectURL: created, revokeObjectURL: revoked });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(`${this.download} ${this.getAttribute("href")} ${document.body.contains(this)}`);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function untilCertError() {
  render(
    <RestartProgressDialog
      targetUrl="https://localhost:8443/programmer"
      alsoTry={[]}
      isProtocolSwitch
      expectsNewCert
      onClose={() => {}}
    />,
  );
  for (let i = 0; i < 40 && !screen.queryByText("Download CA + Open new URL"); i++) {
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  }
  return screen.getByText("Download CA + Open new URL");
}

describe("Download CA + Open new URL", () => {
  it("saves the CA as openavc-ca.crt, then opens the new address", async () => {
    const button = await untilCertError();
    await act(async () => { fireEvent.click(button); });
    expect(downloadCertificate).toHaveBeenCalled();
    expect(saved).toEqual(["openavc-ca.crt blob:ca true"]);
    // Released a turn later: some browsers start the save after click() returns.
    expect(revoked).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(revoked).toHaveBeenCalledWith("blob:ca");
    expect(document.querySelectorAll("a[download]")).toHaveLength(0);
    expect(opened).toHaveBeenCalledWith("https://localhost:8443/programmer", "_blank", "noopener,noreferrer");
  });

  it("still opens the new address when the CA cannot be fetched", async () => {
    downloadCertificate.mockRejectedValueOnce(new Error("No certificate available (HTTP 404)"));
    const button = await untilCertError();
    await act(async () => { fireEvent.click(button); });
    expect(saved).toEqual([]);
    expect(opened).toHaveBeenCalledWith("https://localhost:8443/programmer", "_blank", "noopener,noreferrer");
  });
});
