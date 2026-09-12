import { describe, it, expect } from "vitest";
import { aiErrorDetail, aiMessageForStatus, friendlyAIError } from "./aiErrors";

describe("aiErrorDetail", () => {
  it("returns the server's sentence", () => {
    expect(aiErrorDetail('{"detail":"The assistant is paused on this account."}')).toBe(
      "The assistant is paused on this account."
    );
  });

  it("returns null for a body nobody wrote for this screen", () => {
    expect(aiErrorDetail("<html>502 Bad Gateway</html>")).toBeNull();
    expect(aiErrorDetail("")).toBeNull();
    expect(aiErrorDetail('{"detail":"   "}')).toBeNull();
    expect(aiErrorDetail('{"detail":[{"msg":"field required"}]}')).toBeNull();
    expect(aiErrorDetail('"a bare string"')).toBeNull();
  });
});

describe("aiMessageForStatus", () => {
  // Q-176: a plan refusal, a paused assistant and a cloud that is briefly down
  // all read as one fixed sentence, so "never going to work" and "try again in
  // a minute" were indistinguishable.
  it("relays the sentence the cloud sent for 503", () => {
    expect(
      aiMessageForStatus(503, '{"detail":"The AI assistant is not available right now. Try again shortly."}', "fb")
    ).toBe("The AI assistant is not available right now. Try again shortly.");
  });

  it("relays the sentence the cloud sent for 402", () => {
    expect(
      aiMessageForStatus(402, '{"detail":"Adding a paid space lifts it."}', "fb")
    ).toBe("Adding a paid space lifts it.");
  });

  it("relays the sentence the cloud sent for 429", () => {
    expect(
      aiMessageForStatus(429, '{"detail":"Rate limit exceeded (60 requests/minute). Please wait."}', "fb")
    ).toBe("Rate limit exceeded (60 requests/minute). Please wait.");
  });

  it("falls back to fixed copy when the refusal carried no sentence", () => {
    expect(aiMessageForStatus(503, "<html>503</html>", "fb")).toContain("not available");
    expect(aiMessageForStatus(402, "", "fb")).toContain("subscription");
    expect(aiMessageForStatus(429, "nope", "fb")).toContain("limit reached");
  });

  it("keeps the dropped-connection sentence for 502 and 504", () => {
    // The detail on one of these belongs to whichever hop gave up, not to the
    // assistant, so it is not relayed.
    expect(aiMessageForStatus(502, '{"detail":"Connection to cloud lost."}', "fb")).toBe(
      "The remote connection dropped before the AI answered."
    );
    expect(aiMessageForStatus(504, "", "fb")).toBe(
      "The remote connection dropped before the AI answered."
    );
  });

  it("uses the caller's fallback for a status with no copy of its own", () => {
    expect(aiMessageForStatus(500, "boom", "Couldn't load conversations.")).toBe(
      "Couldn't load conversations."
    );
    expect(aiMessageForStatus(500, '{"detail":"Model not found"}', "fb")).toBe(
      "Model not found"
    );
  });
});

describe("friendlyAIError", () => {
  it("unwraps the aiRequest error shape", () => {
    const e = new Error('AI API 503: {"detail":"The AI assistant is paused on this account."}');
    expect(friendlyAIError(e, "fb")).toBe("The AI assistant is paused on this account.");
  });

  it("passes through anything that isn't an AI API error", () => {
    expect(friendlyAIError(new Error("NetworkError"), "fb")).toBe("NetworkError");
    expect(friendlyAIError(new Error(""), "fb")).toBe("fb");
  });
});
