import { useState, useEffect, useRef, useCallback } from "react";
import { MessageSquare, Plus, Trash2, Undo2, Square, Search, CloudOff } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ChatMessage as ChatMessageComponent } from "../components/ai/ChatMessage";
import { ChatInput } from "../components/ai/ChatInput";
import { PromptCards, SuggestionChips } from "../components/ai/SuggestedPrompts";
import { useAIChatStore } from "../store/aiChatStore";
import { getCloudStatus, getProject } from "../api/restClient";

const listItemStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-md)",
  cursor: "pointer",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-xs)",
};

/** Map streaming phase to user-friendly status text. */
function phaseLabel(phase: string | null, round: { current: number; max: number } | null): string {
  if (!phase) return "";
  if (phase === "thinking") return "Thinking...";
  if (phase === "writing") return "Writing response...";
  // Tool name
  const roundText = round ? ` (step ${round.current})` : "";
  return `Running ${phase}...${roundText}`;
}

export function AIChatView() {
  const available = useAIChatStore((s) => s.available);
  const unavailableReason = useAIChatStore((s) => s.unavailableReason);
  const conversations = useAIChatStore((s) => s.conversations);
  const activeConversationId = useAIChatStore((s) => s.activeConversationId);
  const messages = useAIChatStore((s) => s.messages);
  const loading = useAIChatStore((s) => s.loading);
  const sending = useAIChatStore((s) => s.sending);
  const error = useAIChatStore((s) => s.error);
  const undoStack = useAIChatStore((s) => s.undoStack);
  const streamingPhase = useAIChatStore((s) => s.streamingPhase);
  const currentRound = useAIChatStore((s) => s.currentRound);
  const suggestions = useAIChatStore((s) => s.suggestions);

  const checkAvailability = useAIChatStore((s) => s.checkAvailability);
  const loadConversations = useAIChatStore((s) => s.loadConversations);
  const selectConversation = useAIChatStore((s) => s.selectConversation);
  const newConversation = useAIChatStore((s) => s.newConversation);
  const deleteConversation = useAIChatStore((s) => s.deleteConversation);
  const sendMessage = useAIChatStore((s) => s.sendMessage);
  const stopGeneration = useAIChatStore((s) => s.stopGeneration);
  const setError = useAIChatStore((s) => s.setError);
  const undoMessage = useAIChatStore((s) => s.undoMessage);
  const revertAll = useAIChatStore((s) => s.revertAll);

  const [systemId, setSystemId] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Check AI availability and auto-detect system_id
  useEffect(() => {
    checkAvailability();
    getCloudStatus()
      .then((s) => {
        if (s.system_id) setSystemId(s.system_id);
      })
      .catch(console.error);
  }, [checkAvailability]);

  // Load conversations when available
  useEffect(() => {
    if (available) {
      loadConversations();
    }
  }, [available, loadConversations]);

  // Periodically re-check availability (in case cloud connects/disconnects)
  useEffect(() => {
    if (available) return;
    const interval = setInterval(() => {
      checkAvailability();
    }, 10000);
    return () => clearInterval(interval);
  }, [available, checkAvailability]);

  // Auto-scroll on new content (only if user is at bottom)
  useEffect(() => {
    if (autoScroll) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, sending, autoScroll]);

  // Track scroll position to enable/disable auto-scroll
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    setAutoScroll(atBottom);
  }, []);

  const handleSend = useCallback(
    async (text: string) => {
      setAutoScroll(true);
      // Capture project snapshot before sending (for per-message undo)
      let snap: unknown = null;
      try {
        snap = await getProject();
      } catch {
        // Continue without snapshot
      }
      sendMessage(text, systemId || undefined, snap);
    },
    [sendMessage, systemId]
  );

  const handleRevertAll = useCallback(async () => {
    if (undoStack.length === 0) return;
    if (!confirm(`Revert all AI changes in this conversation? This will undo ${undoStack.length} message(s).`)) return;
    await revertAll();
  }, [undoStack, revertAll]);

  // Not available — show connection message
  if (!available) {
    return (
      <ViewContainer title="AI Assistant">
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
            gap: "var(--space-lg)",
            textAlign: "center",
            padding: "var(--space-xl)",
          }}
        >
          <CloudOff size={48} style={{ color: "var(--text-muted)" }} />
          <h2 style={{ fontSize: "var(--font-size-lg)" }}>AI Assistant</h2>
          <p style={{ color: "var(--text-secondary)", maxWidth: 400 }}>
            AI features require a cloud connection. Pair this system with your
            OpenAVC Cloud account in the Cloud settings to get started.
          </p>
          {unavailableReason && unavailableReason !== "Checking..." && (
            <p style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              {unavailableReason}
            </p>
          )}
        </div>
      </ViewContainer>
    );
  }

  // Available
  return (
    <ViewContainer
      title="AI Assistant"
      actions={
        undoStack.length > 0 ? (
          <button
            onClick={handleRevertAll}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "#e53e3e",
              color: "#fff",
              fontSize: "var(--font-size-sm)",
              border: "none",
              cursor: "pointer",
            }}
            title="Revert all AI changes"
          >
            <Undo2 size={14} /> Revert all
          </button>
        ) : undefined
      }
    >
      <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
        {/* Conversation sidebar */}
        <div
          style={{
            width: 220,
            flexShrink: 0,
            borderRight: "1px solid var(--border-color)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <button
            onClick={newConversation}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              margin: "var(--space-sm)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent)",
              color: "#fff",
              fontSize: "var(--font-size-sm)",
              border: "none",
              cursor: "pointer",
            }}
          >
            <Plus size={14} /> New Chat
          </button>
          {conversations.length > 3 && (
            <div style={{ padding: "0 var(--space-sm) var(--space-xs)", position: "relative" }}>
              <Search
                size={12}
                style={{
                  position: "absolute",
                  left: "calc(var(--space-sm) + 8px)",
                  top: "50%",
                  transform: "translateY(-50%)",
                  color: "var(--text-muted)",
                  pointerEvents: "none",
                }}
              />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search..."
                style={{
                  width: "100%",
                  padding: "4px 8px 4px 24px",
                  fontSize: "var(--font-size-xs)",
                  borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-secondary)",
                  color: "var(--text-primary)",
                  outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>
          )}
          <div style={{ flex: 1, overflow: "auto" }}>
            {conversations.filter((c) =>
              !searchQuery || c.title.toLowerCase().includes(searchQuery.toLowerCase())
            ).map((conv) => (
              <div
                key={conv.id}
                onClick={() => selectConversation(conv.id)}
                style={{
                  ...listItemStyle,
                  background:
                    activeConversationId === conv.id
                      ? "var(--bg-hover)"
                      : "transparent",
                }}
              >
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                  }}
                >
                  {conv.title}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteConversation(conv.id);
                  }}
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text-muted)",
                    padding: 2,
                    flexShrink: 0,
                  }}
                  title="Delete conversation"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
            {conversations.length === 0 && !loading && (
              <p
                style={{
                  color: "var(--text-muted)",
                  fontSize: "var(--font-size-sm)",
                  padding: "var(--space-md)",
                  textAlign: "center",
                }}
              >
                No conversations yet
              </p>
            )}
          </div>
        </div>

        {/* Chat area */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {/* Messages */}
          <div
            ref={scrollContainerRef}
            onScroll={handleScroll}
            style={{ flex: 1, overflow: "auto", padding: "var(--space-lg)" }}
          >
            {messages.length === 0 && (
              <PromptCards onSelect={handleSend} />
            )}
            {messages.map((msg) => {
              const hasUndo = msg.role === "assistant" && !msg.streaming
                && undoStack.some((e) => e.messageId === msg.id);
              return (
                <ChatMessageComponent
                  key={msg.id}
                  message={msg}
                  canUndo={hasUndo}
                  onUndo={hasUndo ? () => undoMessage(msg.id) : undefined}
                />
              );
            })}
            <div ref={messagesEndRef} />
          </div>

          {/* Streaming status bar */}
          {sending && streamingPhase && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "var(--space-xs) var(--space-lg)",
                borderTop: "1px solid var(--border-color)",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-muted)",
              }}
            >
              <div
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: "var(--accent)",
                  animation: "pulse 1s infinite",
                }}
              />
              <span>{phaseLabel(streamingPhase, currentRound)}</span>
            </div>
          )}

          {/* Error */}
          {error && (
            <div
              style={{
                padding: "var(--space-sm) var(--space-md)",
                background: "#fed7d7",
                color: "#9b2c2c",
                fontSize: "var(--font-size-sm)",
              }}
            >
              {error}
              <button
                onClick={() => setError(null)}
                style={{
                  marginLeft: "var(--space-sm)",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  fontWeight: 600,
                }}
              >
                Dismiss
              </button>
            </div>
          )}

          {/* Follow-up suggestions */}
          {!sending && suggestions.length > 0 && (
            <SuggestionChips suggestions={suggestions} onSelect={handleSend} />
          )}

          {/* Input + Stop button */}
          <div style={{ display: "flex", alignItems: "flex-end", gap: 0 }}>
            <div style={{ flex: 1 }}>
              <ChatInput
                onSend={handleSend}
                disabled={sending}
                placeholder="Describe what you want to build or fix..."
              />
            </div>
            {sending && (
              <button
                onClick={stopGeneration}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 36,
                  height: 36,
                  margin: "var(--space-sm)",
                  marginLeft: 0,
                  borderRadius: "var(--border-radius)",
                  background: "#e53e3e",
                  color: "#fff",
                  border: "none",
                  cursor: "pointer",
                  flexShrink: 0,
                }}
                title="Stop generation"
              >
                <Square size={14} />
              </button>
            )}
          </div>
        </div>
      </div>
      <style>{`@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }`}</style>
    </ViewContainer>
  );
}
