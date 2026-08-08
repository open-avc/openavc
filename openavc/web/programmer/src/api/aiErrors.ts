// Friendly messages for AI API failures. The streaming chat path maps
// 429/402/503 inline (it has the raw Response); the non-streaming
// conversation list/select/delete paths throw `Error("AI API <status>:
// <body>")` from aiRequest — this turns those into the same user-facing
// copy instead of surfacing raw JSON.

export function friendlyAIError(e: unknown, fallback: string): string {
  const msg = e instanceof Error ? e.message : String(e);
  const m = msg.match(/^AI API (\d+): ?([\s\S]*)$/);
  if (!m) return msg || fallback;
  const status = Number(m[1]);
  if (status === 429) {
    return "AI request limit reached. Please try again later or upgrade your plan.";
  }
  if (status === 402) {
    return "AI features require an active subscription.";
  }
  if (status === 503) {
    return "AI is not available. Make sure this system is paired and connected to the cloud.";
  }
  try {
    const detail = (JSON.parse(m[2]) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  } catch {
    // body wasn't JSON — fall through to the fallback
  }
  return fallback;
}
