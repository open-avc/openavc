/**
 * Cloud AI API client — communicates directly with OpenAVC Cloud
 * for AI chat features. Separate from restClient which talks to the local instance.
 */

// --- Cloud auth state (persisted in sessionStorage — not localStorage to reduce XSS risk) ---

const CLOUD_TOKEN_KEY = "openavc_cloud_token";
const CLOUD_REFRESH_TOKEN_KEY = "openavc_cloud_refresh_token";
const CLOUD_ENDPOINT_KEY = "openavc_cloud_endpoint";

export function getCloudToken(): string | null {
  return sessionStorage.getItem(CLOUD_TOKEN_KEY);
}

export function setCloudToken(token: string): void {
  sessionStorage.setItem(CLOUD_TOKEN_KEY, token);
}

export function getCloudEndpoint(): string | null {
  return sessionStorage.getItem(CLOUD_ENDPOINT_KEY);
}

export function setCloudEndpoint(endpoint: string): void {
  sessionStorage.setItem(CLOUD_ENDPOINT_KEY, endpoint);
}

export function getRefreshToken(): string | null {
  return sessionStorage.getItem(CLOUD_REFRESH_TOKEN_KEY);
}

export function setRefreshToken(token: string): void {
  sessionStorage.setItem(CLOUD_REFRESH_TOKEN_KEY, token);
}

export function clearCloudAuth(): void {
  sessionStorage.removeItem(CLOUD_TOKEN_KEY);
  sessionStorage.removeItem(CLOUD_REFRESH_TOKEN_KEY);
  sessionStorage.removeItem(CLOUD_ENDPOINT_KEY);
}

/** Listeners notified when auth state changes (e.g., on 401). */
let authChangeListeners: Array<() => void> = [];
export function onAuthChange(handler: () => void): () => void {
  authChangeListeners.push(handler);
  return () => {
    authChangeListeners = authChangeListeners.filter((h) => h !== handler);
  };
}
function notifyAuthChange() {
  for (const h of authChangeListeners) h();
}

export function isCloudAuthenticated(): boolean {
  return !!getCloudToken() && !!getCloudEndpoint();
}

// --- Cloud API request helper ---

let refreshInProgress: Promise<boolean> | null = null;

async function tryRefreshToken(): Promise<boolean> {
  const endpoint = getCloudEndpoint();
  const refreshToken = getRefreshToken();
  if (!endpoint || !refreshToken) return false;

  try {
    const res = await fetch(`${endpoint}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (data.access_token) {
      setCloudToken(data.access_token);
      if (data.refresh_token) setRefreshToken(data.refresh_token);
      return true;
    }
  } catch {
    // Refresh failed — fall through
  }
  return false;
}

async function cloudRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const endpoint = getCloudEndpoint();
  const token = getCloudToken();
  if (!endpoint || !token) {
    throw new Error("Not authenticated with cloud");
  }

  let res = await fetch(`${endpoint}/api/v1${path}`, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    ...options,
  });

  // On 401, attempt a silent token refresh
  if (res.status === 401) {
    if (!refreshInProgress) {
      refreshInProgress = tryRefreshToken().finally(() => {
        refreshInProgress = null;
      });
    }
    const refreshed = await refreshInProgress;
    if (refreshed) {
      // Retry with new token
      res = await fetch(`${endpoint}/api/v1${path}`, {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getCloudToken()}`,
        },
        ...options,
      });
    }
    if (res.status === 401) {
      clearCloudAuth();
      notifyAuthChange();
      throw new Error("Cloud session expired. Please log in again.");
    }
  }

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Cloud API ${res.status}: ${body}`);
  }

  // Handle empty responses (e.g., 204 No Content)
  const contentType = res.headers.get("content-type");
  if (!contentType || !contentType.includes("application/json")) {
    return undefined as T;
  }
  return res.json();
}

// --- Cloud auth ---

export interface CloudLoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export async function cloudLogin(
  endpoint: string,
  email: string,
  password: string
): Promise<CloudLoginResponse> {
  const res = await fetch(`${endpoint}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Login failed: ${body}`);
  }
  const data: CloudLoginResponse = await res.json();
  setCloudEndpoint(endpoint);
  setCloudToken(data.access_token);
  if (data.refresh_token) setRefreshToken(data.refresh_token);
  return data;
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
  return cloudRequest<ChatResponse>("/ai/chat", {
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
 * Uses fetch + ReadableStream (not EventSource) because we need POST + auth headers.
 */
export function streamChatMessage(
  req: ChatRequest,
  callbacks: StreamCallbacks,
): AbortController {
  const controller = new AbortController();
  const endpoint = getCloudEndpoint();
  const token = getCloudToken();

  if (!endpoint || !token) {
    callbacks.onError?.("Not authenticated with cloud");
    return controller;
  }

  (async () => {
    try {
      const res = await fetch(`${endpoint}/api/v1/ai/chat?stream=true`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
        },
        body: JSON.stringify(req),
        signal: controller.signal,
      });

      if (res.status === 401) {
        // Try token refresh
        if (!refreshInProgress) {
          refreshInProgress = tryRefreshToken().finally(() => {
            refreshInProgress = null;
          });
        }
        const refreshed = await refreshInProgress;
        if (!refreshed) {
          clearCloudAuth();
          notifyAuthChange();
          callbacks.onError?.("Cloud session expired. Please log in again.");
          return;
        }
        // Retry with new token (non-streaming fallback if refresh worked)
        const retryRes = await sendChatMessage(req);
        callbacks.onDone?.({
          conversation_id: retryRes.conversation_id,
          message: retryRes.message,
          input_tokens: retryRes.input_tokens,
          output_tokens: retryRes.output_tokens,
          tool_calls: retryRes.tool_calls,
        });
        return;
      }

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
        } else {
          callbacks.onError?.(`Cloud API ${res.status}: ${body}`);
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
  return cloudRequest<ConversationSummary[]>("/ai/conversations");
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  return cloudRequest<ConversationDetail>(`/ai/conversations/${id}`);
}

export async function deleteConversation(id: string): Promise<void> {
  await cloudRequest(`/ai/conversations/${id}`, { method: "DELETE" });
}

export async function getAIUsage(): Promise<AIUsage> {
  return cloudRequest<AIUsage>("/ai/usage");
}
