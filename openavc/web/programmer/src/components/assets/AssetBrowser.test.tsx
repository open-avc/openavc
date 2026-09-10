import { describe, expect, it } from "vitest";

import { deleteMessage } from "./AssetBrowser";
import type { AssetInfo } from "../../api/systemClient";

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
