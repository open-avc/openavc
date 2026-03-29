/**
 * AI API client — communicates with the local server's AI proxy endpoints.
 *
 * The local server proxies requests to the cloud using system-level HMAC auth
 * (established during pairing). No separate cloud login is needed.
 */

// Derive API base path so tunneled remote access works.
function getBasePath(): string {
  const pathParts = window.location.pathname.split("/programmer");
  const prefix = pathParts[0] || "";
  return `${prefix}/api/ai`;
}
const AI_BASE = getBasePath();

async function aiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${AI_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`AI API ${res.status}: ${body}`);
  }

  // Handle empty responses (e.g., 204 No Content)
  const contentType = res.headers.get("content-type");
  if (!contentType || !contentType.includes("application/json")) {
    return undefined as T;
  }
  return res.json();
}

// --- AI status ---

export interface AIStatus {
  available: boolean;
  reason?: string;
}

export async function getAIStatus(): Promise<AIStatus> {
  return aiRequest<AIStatus>("/status");
}

// --- AI Chat API ---

export interface ChatRequest {
  message: string;
  conversation_id?: string;
  system_id?: string;
}

export interface ChatResponse {
  conversation_id: string;
  message: string;
  input_tokens: number;
  output_tokens: number;
  tool_calls: { id: string; name: string; input: Record<string, unknown> }[] | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  system_id: string | null;
  message_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: string;
  content: string;
  input_tokens: number;
  output_tokens: number;
  tool_calls: Record<string, unknown> | null;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
}

export interface AIUsage {
  requests_used: number;
  requests_limit: number | null;
  requests_remaining: number | null;
  current_period_start: string;
  current_period_end: string;
}

export async function sendChatMessage(req: ChatRequest): Promise<ChatResponse> {
  return aiRequest<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// --- SSE streaming chat ---

export interface StreamCallbacks {
  onStatus?: (phase: string) => void;
  onTextDelta?: (text: string) => void;
  onToolUseStart?: (id: string, name: string) => void;
  onToolResult?: (data: {
    id: string;
    name: string;
    input: Record<string, unknown>;
    success: boolean;
    summary: string;
    duration_ms: number;
  }) => void;
  onRound?: (current: number, max: number) => void;
  onDone?: (data: {
    conversation_id: string;
    message: string;
    input_tokens: number;
    output_tokens: number;
    tool_calls: { id: string; name: string; input: Record<string, unknown> }[] | null;
    suggestions?: string[];
    title?: string;
  }) => void;
  onError?: (message: string) => void;
}

/**
 * Stream a chat message via SSE. Returns an AbortController to cancel.
 */
export function streamChatMessage(
  req: ChatRequest,
  callbacks: StreamCallbacks,
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${AI_BASE}/chat?stream=true`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(req),
        signal: controller.signal,
      });

      if (!res.ok) {
        const body = await res.text();
        if (res.status === 429) {
          callbacks.onError?.(
            "AI request limit reached. Please try again later or upgrade your plan."
          );
        } else if (res.status === 402) {
          callbacks.onError?.(
            "AI features require an active subscription."
          );
        } else if (res.status === 503) {
          callbacks.onError?.(
            "AI is not available. Make sure this system is paired and connected to the cloud."
          );
        } else {
          callbacks.onError?.(`AI API ${res.status}: ${body}`);
        }
        return;
      }

      // Parse SSE stream
      const reader = res.body?.getReader();
      if (!reader) {
        callbacks.onError?.("No response body");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE messages (separated by double newlines)
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          if (!part.trim()) continue;

          let eventType = "";
          let eventData = "";

          for (const line of part.split("\n")) {
            if (line.startsWith("event: ")) {
              eventType = line.slice(7);
            } else if (line.startsWith("data: ")) {
              eventData = line.slice(6);
            }
          }

          if (!eventType || !eventData) continue;

          try {
            const data = JSON.parse(eventData);
            switch (eventType) {
              case "status":
                callbacks.onStatus?.(data.phase);
                break;
              case "text_delta":
                callbacks.onTextDelta?.(data.text);
                break;
              case "tool_use_start":
                callbacks.onToolUseStart?.(data.id, data.name);
                break;
              case "tool_result":
                callbacks.onToolResult?.(data);
                break;
              case "round":
                callbacks.onRound?.(data.current, data.max);
                break;
              case "done":
                callbacks.onDone?.(data);
                break;
              case "error":
                callbacks.onError?.(data.message || "Unknown error");
                break;
            }
          } catch {
            // Skip malformed JSON
          }
        }
      }
    } catch (err) {
      if (controller.signal.aborted) return; // User cancelled
      callbacks.onError?.(String(err));
    }
  })();

  return controller;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  return aiRequest<ConversationSummary[]>("/conversations");
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  return aiRequest<ConversationDetail>(`/conversations/${id}`);
}

export async function deleteConversation(id: string): Promise<void> {
  await aiRequest(`/conversations/${id}`, { method: "DELETE" });
}

export async function getAIUsage(): Promise<AIUsage> {
  return aiRequest<AIUsage>("/usage");
}
