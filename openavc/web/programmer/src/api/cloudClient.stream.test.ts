import { afterEach, describe, expect, it, vi } from "vitest";
import { streamChatMessage } from "./cloudClient";

// A streamed send the cloud refuses comes back as an error event. One that
// refuses for the system's release carries the release to install beside its
// sentence, and the store needs both.

function streamOf(...events: string[]) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(events.join(""), { status: 200 })));
}

afterEach(() => vi.unstubAllGlobals());

describe("a refused streamed send", () => {
  it("hands over the release to install with the sentence", async () => {
    streamOf('event: error\ndata: {"message": "Update it to v0.42.0.", "needs_release": "0.42.0"}\n\n');
    const onError = vi.fn();
    streamChatMessage({ message: "hi" }, { onError });
    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onError).toHaveBeenCalledWith("Update it to v0.42.0.", "0.42.0");
  });

  it("hands over the sentence alone for any other refusal", async () => {
    streamOf('event: error\ndata: {"message": "Paused.", "needs_release": 42}\n\n');
    const onError = vi.fn();
    streamChatMessage({ message: "hi" }, { onError });
    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onError).toHaveBeenCalledWith("Paused.", undefined);
  });
});
