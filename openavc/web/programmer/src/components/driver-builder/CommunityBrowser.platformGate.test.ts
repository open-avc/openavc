import { describe, it, expect } from "vitest";
import { blockedByPlatform } from "./CommunityBrowser";

// The catalog has always carried min_platform_version and the server has always
// enforced it (422, "requires OpenAVC X or later"). Nothing SHOWED it: the card
// rendered a live Install button and the refusal only arrived after the click.
//
// That was a rare case until the `server` -> `openavc` package rename in
// 0.25.0, which gated every Python driver in the catalog at once. On a 0.24.x
// box the whole Python half of the library then looked installable and none of
// it was.

describe("blockedByPlatform", () => {
  it("blocks a driver that needs a newer platform than this one", () => {
    expect(blockedByPlatform("0.24.1", "0.25.0")).toBe("0.25.0");
  });

  it("allows a driver whose requirement this platform already meets", () => {
    expect(blockedByPlatform("0.25.0", "0.25.0")).toBe("");
    expect(blockedByPlatform("0.26.0", "0.25.0")).toBe("");
  });

  it("returns the required version, so the caller can name it", () => {
    // "Needs OpenAVC v0.25.0" is the deliverable -- "unavailable" is not
    // actionable, and the whole point is telling someone what to do next.
    expect(blockedByPlatform("0.24.1", "0.25.0")).toBe("0.25.0");
  });

  it("never blocks when the driver declares no minimum", () => {
    expect(blockedByPlatform("0.24.1", null)).toBe("");
    expect(blockedByPlatform("0.24.1", undefined)).toBe("");
    expect(blockedByPlatform("0.24.1", "")).toBe("");
  });

  it("never blocks when the running version is not known yet", () => {
    // liveState is empty until the first WebSocket snapshot arrives. Guessing
    // "blocked" there would grey out the entire catalog on every page load.
    expect(blockedByPlatform("", "0.25.0")).toBe("");
  });

  it("never blocks on a version string it cannot read", () => {
    // The server is the authority; this check only moves the message earlier.
    // A garbled version must fail open, not hide an installable driver.
    expect(blockedByPlatform("not-a-version", "0.25.0")).toBe("");
    expect(blockedByPlatform("0.24.1", "not-a-version")).toBe("");
  });

  it("treats a prerelease as older than its release", () => {
    // A 0.25.0-rc1 box does not satisfy a 0.25.0 requirement.
    expect(blockedByPlatform("0.25.0-rc1", "0.25.0")).toBe("0.25.0");
  });

  it("compares numerically, not as strings", () => {
    // "0.9.0" > "0.25.0" lexically. This is the case a string compare gets
    // wrong, and it is reachable: 0.9.x boxes exist.
    expect(blockedByPlatform("0.9.0", "0.25.0")).toBe("0.25.0");
    expect(blockedByPlatform("0.25.0", "0.9.0")).toBe("");
  });
});
