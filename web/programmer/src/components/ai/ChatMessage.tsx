import { Bot, User, Undo2 } from "lucide-react";
import type { Message } from "../../store/aiChatStore";
import { ToolCallBlock } from "./ToolCallBlock";

interface ChatMessageProps {
  message: Message;
  canUndo?: boolean;
  onUndo?: () => void;
}

const bubbleBase: React.CSSProperties = {
  padding: "var(--space-md)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  lineHeight: 1.6,
  maxWidth: "85%",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

const userBubble: React.CSSProperties = {
  ...bubbleBase,
  background: "var(--accent)",
  color: "#fff",
  marginLeft: "auto",
};

const assistantBubble: React.CSSProperties = {
  ...bubbleBase,
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  color: "var(--text-primary)",
};

export function ChatMessage({ message, canUndo, onUndo }: ChatMessageProps) {
  const isUser = message.role === "user";

  // Show live tool calls (streaming) or persisted tool calls
  const liveTools = message.liveToolCalls;
  const persistedTools = message.toolCalls;
  const hasLiveTools = liveTools && liveTools.length > 0;
  const hasPersistedTools = persistedTools && persistedTools.length > 0;

  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "flex-start",
        marginBottom: "var(--space-md)",
        flexDirection: isUser ? "row-reverse" : "row",
      }}
    >
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: "50%",
          background: isUser ? "var(--accent)" : "var(--bg-hover)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {isUser ? <User size={14} color="#fff" /> : <Bot size={14} />}
      </div>
      <div style={{ minWidth: 0, maxWidth: "85%" }}>
        <div style={isUser ? userBubble : assistantBubble}>
          {message.content}
          {message.streaming && (
            <span
              style={{
                display: "inline-block",
                width: 6,
                height: 14,
                background: "var(--accent)",
                marginLeft: 2,
                verticalAlign: "text-bottom",
                animation: "blink 1s step-end infinite",
              }}
            />
          )}
        </div>

        {/* Live tool calls during streaming */}
        {hasLiveTools && (
          <div style={{ marginTop: "var(--space-xs)" }}>
            {liveTools.map((tc) => (
              <ToolCallBlock key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}

        {/* Persisted tool calls (from loaded conversations) */}
        {!hasLiveTools && hasPersistedTools && (
          <div style={{ marginTop: "var(--space-xs)" }}>
            {persistedTools.map((tc) => (
              <ToolCallBlock key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}

        {/* Token count + undo (after streaming completes) */}
        {!isUser && !message.streaming && (
          <div
            style={{
              marginTop: "var(--space-xs)",
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              gap: "var(--space-sm)",
            }}
          >
            {(message.inputTokens || message.outputTokens) && (
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                {message.inputTokens?.toLocaleString()} in / {message.outputTokens?.toLocaleString()} out
              </span>
            )}
            {canUndo && onUndo && (
              <button
                onClick={onUndo}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 3,
                  padding: "1px 6px",
                  borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-secondary)",
                  color: "var(--text-secondary)",
                  fontSize: 10,
                  cursor: "pointer",
                }}
                title="Undo changes from this response"
              >
                <Undo2 size={10} /> Undo
              </button>
            )}
          </div>
        )}

        <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
      </div>
    </div>
  );
}
