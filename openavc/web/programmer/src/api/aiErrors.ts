// Friendly messages for AI API failures. The streaming chat path and the
// non-streaming conversation list/select/delete paths both end up here, so a
// refusal reads the same wherever it arrived.
//
// The server relays the cloud's own sentence whenever the cloud sent one
// (ai_proxy._error_message), and the cloud's sentences are the specific ones:
// an account past its allowance is told what lifts it, a paused assistant is
// told to get in touch, a service that is briefly down is told to come back.
// So a parsed `detail` wins over the fixed copy below, which answers for a
// refusal that arrived without a sentence -- an intermediary's HTML 503, a
// body that is not JSON at all.

const RELAYED_FALLBACKS: Record<number, string> = {
  429: "AI request limit reached. Please try again later or upgrade your plan.",
  402: "AI features require an active subscription.",
  503: "AI is not available. Make sure this system is paired and connected to the cloud.",
};

/** The server's own sentence out of an error body, or null if it sent none. */
export function aiErrorDetail(body: string): string | null {
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
  } catch {
    // Not JSON, so not a sentence anyone wrote for this screen.
  }
  return null;
}

/** What to show for an AI request that came back with `status` and `body`. */
export function aiMessageForStatus(
  status: number,
  body: string,
  fallback: string
): string {
  // Reached over a remote connection that gave up before the answer came back.
  // Nothing about the raw status tells anyone that, and the detail on one of
  // these belongs to whichever hop gave up rather than to the assistant.
  if (status === 502 || status === 504) {
    return "The remote connection dropped before the AI answered.";
  }
  const detail = aiErrorDetail(body);
  if (detail) return detail;
  return RELAYED_FALLBACKS[status] ?? fallback;
}

export function friendlyAIError(e: unknown, fallback: string): string {
  const msg = e instanceof Error ? e.message : String(e);
  const m = msg.match(/^AI API (\d+): ?([\s\S]*)$/);
  if (!m) return msg || fallback;
  return aiMessageForStatus(Number(m[1]), m[2], fallback);
}
