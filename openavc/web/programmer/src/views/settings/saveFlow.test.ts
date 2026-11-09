import { describe, it, expect } from "vitest";
import { afterSave, changesToSend, questionUrls, saveFailure } from "./saveFlow";
import { ApiError } from "../../api/errors";
import type { SystemConfig } from "../../api/restClient";

describe("changesToSend", () => {
  it("sends every change, section by section", () => {
    expect(changesToSend({ logging: { level: "debug" }, network: { http_port: 9090 } } as Partial<SystemConfig>))
      .toEqual({ logging: { level: "debug" }, network: { http_port: 9090 } });
  });

  it("leaves out a secret sent back as the server gave it, and a section left empty", () => {
    expect(changesToSend({
      auth: { api_key: "***", programmer_password: "***" },
      logging: { level: "info", file_enabled: false },
      tls: { cert_file: "***", enabled: true },
    } as unknown as Partial<SystemConfig>)).toEqual({
      logging: { level: "info", file_enabled: false },
      tls: { enabled: true },
    });
  });

  it("keeps a secret that is anything but the server's placeholder, empty included", () => {
    expect(changesToSend({ auth: { api_key: "", programmer_password: "****" } } as unknown as Partial<SystemConfig>))
      .toEqual({ auth: { api_key: "", programmer_password: "****" } });
  });

  it("sends nothing for nothing", () => {
    expect(changesToSend({})).toEqual({});
  });
});

describe("afterSave", () => {
  const none = { kioskChanged: false, kioskAvailable: false, cutOffConfirmed: false, needsRestart: false };

  it("asks the kiosk's reboot question first, where a kiosk runs", () => {
    expect(afterSave({ kioskChanged: true, kioskAvailable: true, cutOffConfirmed: true, needsRestart: true })).toBe("reboot");
    expect(afterSave({ ...none, kioskChanged: true, kioskAvailable: false })).toBeNull();
    expect(afterSave({ ...none, kioskChanged: false, kioskAvailable: true })).toBeNull();
  });

  it("restarts at once when the cut-off question was already answered", () => {
    expect(afterSave({ ...none, cutOffConfirmed: true, needsRestart: true })).toBe("restart");
    expect(afterSave({ ...none, cutOffConfirmed: true })).toBe("restart");
  });

  it("asks for the restart a change needs, else nothing", () => {
    expect(afterSave({ ...none, needsRestart: true })).toBe("ask-restart");
    expect(afterSave(none)).toBeNull();
  });
});

describe("questionUrls", () => {
  it("uses HTTPS and its port when HTTPS is on screen", () => {
    expect(questionUrls({ enabled: true, port: 9443 }, 8080, ["10.20.30.5", "10.20.30.6"])).toEqual({
      programmerUrls: ["https://10.20.30.5:9443/programmer", "https://10.20.30.6:9443/programmer"],
      localUrl: "https://localhost:9443/programmer",
    });
  });

  it("uses 8443 for HTTPS with no port", () => {
    expect(questionUrls({ enabled: true }, 8080, [])).toEqual({ programmerUrls: [], localUrl: "https://localhost:8443/programmer" });
  });

  it("uses the HTTP port on screen while HTTPS is off or unknown", () => {
    expect(questionUrls({ enabled: false, port: 9443 }, 8181, ["10.20.30.5"]).programmerUrls).toEqual(["http://10.20.30.5:8181/programmer"]);
    expect(questionUrls(undefined, 8181, []).localUrl).toBe("http://localhost:8181/programmer");
  });
});

describe("saveFailure", () => {
  it("says the save failed when it did not go through", () => {
    expect(saveFailure(new ApiError(400, JSON.stringify({ detail: "Bad port" })), false)).toBe("Failed to save: Bad port");
    expect(saveFailure(new ApiError(401, JSON.stringify({ detail: "Authentication required" })), false))
      .toBe("Failed to save: Authentication required");
  });

  it("says nothing for a 401 after the save went through: the save ended the session", () => {
    expect(saveFailure(new ApiError(401, JSON.stringify({ detail: "Authentication required" })), true)).toBeNull();
  });

  it("says the save went through when only the read after it failed", () => {
    expect(saveFailure(new ApiError(500, JSON.stringify({ detail: "Server error" })), true)).toBe(
      "Settings saved, but this page could not refresh (Server error). Reload the page to see the current values.",
    );
  });
});
