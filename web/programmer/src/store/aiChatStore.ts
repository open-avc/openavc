/**
 * AI Chat store — manages conversations, messages, streaming state, and undo.
 */

import { create } from "zustand";
import * as cloud from "../api/cloudClient";

export interface LiveToolCall {
  id: string;
  name: string;
  input?: Record<string, unknown>;
  status: "running" | "success" | "error";
  summary?: string;
  durationMs?: number;
}

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool"; toolCall: LiveToolCall };

export interface Message {
  id: string;
  role: "user" | "assistant" | "tool_use" | "tool_result";
  content: string;
  toolCalls?: { id: string; name: string; input: Record<string, unknown> }[] | null;
  createdAt: string;
  // Streaming fields
  streaming?: boolean;
  liveToolCalls?: LiveToolCall[];
  contentBlocks?: ContentBlock[];
  inputTokens?: number;
  outputTokens?: number;
}

interface AIChatStore {
  // AI availability (cloud paired + connected)
  available: boolean;
  unavailableReason: string;

  // Conversations
  conversations: cloud.ConversationSummary[];
  activeConversationId: string | null;
  messages: Message[];

  // State
  loading: boolean;
  sending: boolean;
  error: string | null;

  // Streaming
  streamingAbort: AbortController | null;
  streamingPhase: string | null; // "thinking", "writing", tool name, etc.
  currentRound: { current: number; max: number } | null;

  // Suggestions
  suggestions: string[];

  // Undo
  undoStack: { messageId: string; snapshot: unknown }[];

  // Actions
  checkAvailability: () => Promise<void>;
  loadConversations: () => Promise<void>;
  selectConversation: (id: string) => Promise<void>;
  newConversation: () => void;
  deleteConversation: (id: string) => Promise<void>;
  sendMessage: (text: string, systemId?: string, snapshot?: unknown) => void;
  stopGeneration: () => void;
  setError: (error: string | null) => void;
  pushSnapshot: (messageId: string, snapshot: unknown) => void;
  undoMessage: (messageId: string) => Promise<void>;
  revertAll: () => Promise<void>;
}

export const useAIChatStore = create<AIChatStore>((set, get) => ({
  available: false,
  unavailableReason: "Checking...",
  conversations: [],
  activeConversationId: null,
  messages: [],
  loading: false,
  sending: false,
  error: null,
  streamingAbort: null,
  streamingPhase: null,
  currentRound: null,
  suggestions: [],
  undoStack: [],

  checkAvailability: async () => {
    try {
      const status = await cloud.getAIStatus();
      set({
        available: status.available,
        unavailableReason: status.reason || "",
      });
    } catch {
      set({ available: false, unavailableReason: "Could not check AI status" });
    }
  },

  loadConversations: async () => {
    set({ loading: true, error: null });
    try {
      const conversations = await cloud.listConversations();
      set({ conversations, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  selectConversation: async (id: string) => {
    set({ loading: true, error: null, activeConversationId: id, undoStack: [] });
    try {
      const detail = await cloud.getConversation(id);
      const messages: Message[] = detail.messages.map((m) => {
        const msg: Message = {
          id: m.id,
          role: m.role as Message["role"],
          content: m.content,
          toolCalls: m.tool_calls as Message["toolCalls"],
          createdAt: m.created_at,
        };
        // Build content blocks from persisted data (text + tools interleaved)
        if (msg.role === "assistant") {
          const blocks: ContentBlock[] = [];
          if (msg.content) blocks.push({ type: "text", text: msg.content });
          if (msg.toolCalls) {
            for (const tc of msg.toolCalls) {
              blocks.push({ type: "tool", toolCall: { ...tc, status: "success" as const } });
            }
          }
          msg.contentBlocks = blocks;
        }
        return msg;
      });
      set({ messages, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  newConversation: () => {
    set({ activeConversationId: null, messages: [], undoStack: [] });
  },

  deleteConversation: async (id: string) => {
    try {
      await cloud.deleteConversation(id);
      const { conversations, activeConversationId } = get();
      set({
        conversations: conversations.filter((c) => c.id !== id),
        ...(activeConversationId === id
          ? { activeConversationId: null, messages: [], undoStack: [] }
          : {}),
      });
    } catch (e) {
      set({ error: String(e) });
    }
  },

  sendMessage: (text: string, systemId?: string, snapshot?: unknown) => {
    const { activeConversationId: convId } = get();

    // Add user message optimistically
    const userMsg: Message = {
      id: `temp_${Date.now()}`,
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
    };

    // Add placeholder assistant message for streaming
    const assistantMsg: Message = {
      id: `stream_${Date.now()}`,
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
      streaming: true,
      liveToolCalls: [],
    };

    const assistantId = assistantMsg.id;

    set((s) => {
      const undoStack = snapshot
        ? [...s.undoStack, { messageId: assistantId, snapshot }].slice(-10)
        : s.undoStack;
      return {
        messages: [...s.messages, userMsg, assistantMsg],
        sending: true,
        error: null,
        streamingPhase: "thinking",
        currentRound: null,
        suggestions: [],
        undoStack,
      };
    });

    const controller = cloud.streamChatMessage(
      {
        message: text,
        conversation_id: convId || undefined,
        system_id: systemId,
      },
      {
        onStatus: (phase) => {
          set({ streamingPhase: phase });
        },

        onTextDelta: (deltaText) => {
          set((s) => ({
            streamingPhase: "writing",
            messages: s.messages.map((m) => {
              if (m.id !== assistantId) return m;
              const blocks = [...(m.contentBlocks || [])];
              const last = blocks[blocks.length - 1];
              if (last && last.type === "text") {
                blocks[blocks.length - 1] = { type: "text", text: last.text + deltaText };
              } else {
                blocks.push({ type: "text", text: deltaText });
              }
              return { ...m, content: m.content + deltaText, contentBlocks: blocks };
            }),
          }));
        },

        onToolUseStart: (id, name) => {
          set((s) => ({
            streamingPhase: name,
            messages: s.messages.map((m) => {
              if (m.id !== assistantId) return m;
              const tc: LiveToolCall = { id, name, status: "running" as const };
              const blocks = [...(m.contentBlocks || []), { type: "tool" as const, toolCall: tc }];
              return {
                ...m,
                liveToolCalls: [...(m.liveToolCalls || []), tc],
                contentBlocks: blocks,
              };
            }),
          }));
        },

        onToolResult: (data) => {
          set((s) => ({
            messages: s.messages.map((m) => {
              if (m.id !== assistantId) return m;
              const updateTc = (tc: LiveToolCall): LiveToolCall =>
                tc.id === data.id
                  ? { ...tc, input: data.input, status: (data.success ? "success" : "error") as "success" | "error", summary: data.summary, durationMs: data.duration_ms }
                  : tc;
              return {
                ...m,
                liveToolCalls: (m.liveToolCalls || []).map(updateTc),
                contentBlocks: (m.contentBlocks || []).map((b) =>
                  b.type === "tool" && b.toolCall.id === data.id
                    ? { type: "tool" as const, toolCall: updateTc(b.toolCall) }
                    : b
                ),
              };
            }),
          }));
        },

        onRound: (current, max) => {
          set({ currentRound: { current, max } });
        },

        onDone: (data) => {
          // Finalize the assistant message — keep interleaved content blocks
          set((s) => ({
            sending: false,
            streamingAbort: null,
            streamingPhase: null,
            currentRound: null,
            activeConversationId: data.conversation_id,
            suggestions: data.suggestions || [],
            messages: s.messages.map((m) => {
              if (m.id !== assistantId) return m;
              // Update tool calls in blocks with final data from done event
              let blocks = m.contentBlocks;
              if (blocks && data.tool_calls) {
                const tcMap = new Map(
                  (data.tool_calls as { id: string; name: string; input: Record<string, unknown> }[])
                    .map((tc) => [tc.id, tc])
                );
                blocks = blocks.map((b) => {
                  if (b.type !== "tool") return b;
                  const final = tcMap.get(b.toolCall.id);
                  if (!final) return b;
                  return { type: "tool" as const, toolCall: { ...b.toolCall, input: final.input } };
                });
              }
              return {
                ...m,
                content: data.message || m.content,
                streaming: false,
                liveToolCalls: undefined,
                toolCalls: data.tool_calls,
                contentBlocks: blocks,
                inputTokens: data.input_tokens,
                outputTokens: data.output_tokens,
              };
            }),
          }));

          // Refresh conversation list
          get().loadConversations();
        },

        onError: (message) => {
          // Remove the placeholder messages
          set((s) => ({
            messages: s.messages.filter(
              (m) => m.id !== userMsg.id && m.id !== assistantId
            ),
            error: message,
            sending: false,
            streamingAbort: null,
            streamingPhase: null,
            currentRound: null,
          }));
        },
      },
    );

    set({ streamingAbort: controller });
  },

  stopGeneration: () => {
    const { streamingAbort } = get();
    if (streamingAbort) {
      streamingAbort.abort();
    }

    // Finalize any streaming message
    set((s) => ({
      sending: false,
      streamingAbort: null,
      streamingPhase: null,
      currentRound: null,
      messages: s.messages.map((m) =>
        m.streaming
          ? {
              ...m,
              streaming: false,
              content: m.content + "\n\n[Stopped by user]",
            }
          : m
      ),
    }));
  },

  setError: (error) => set({ error }),

  pushSnapshot: (messageId, snapshot) => {
    set((s) => {
      const stack = [...s.undoStack, { messageId, snapshot }];
      // Limit to 10 entries
      if (stack.length > 10) stack.shift();
      return { undoStack: stack };
    });
  },

  undoMessage: async (messageId) => {
    const { undoStack } = get();
    const idx = undoStack.findIndex((e) => e.messageId === messageId);
    if (idx < 0) return;

    const entry = undoStack[idx];
    try {
      const { saveProject, reloadProject } = await import("../api/restClient");
      await saveProject(entry.snapshot as Parameters<typeof saveProject>[0]);
      await reloadProject();
      // Remove this entry and all after it from the stack
      // Remove the user+assistant message pair and everything after
      set((s) => ({
        undoStack: s.undoStack.slice(0, idx),
        messages: s.messages.filter((m) => {
          const mIdx = s.messages.indexOf(m);
          // Find the user message that precedes this assistant message
          const assistantIdx = s.messages.findIndex((msg) => msg.id === messageId);
          const userIdx = assistantIdx > 0 ? assistantIdx - 1 : assistantIdx;
          return mIdx < userIdx;
        }),
        suggestions: [],
        error: null,
      }));
    } catch (e) {
      set({ error: `Undo failed: ${e}` });
    }
  },

  revertAll: async () => {
    const { undoStack } = get();
    if (undoStack.length === 0) return;

    const oldest = undoStack[0];
    try {
      const { saveProject, reloadProject } = await import("../api/restClient");
      await saveProject(oldest.snapshot as Parameters<typeof saveProject>[0]);
      await reloadProject();
      set({ undoStack: [], messages: [], suggestions: [], error: null });
    } catch (e) {
      set({ error: `Revert failed: ${e}` });
    }
  },
}));
