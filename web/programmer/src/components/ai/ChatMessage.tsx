import { Bot, User, Undo2 } from "lucide-react";
import type { Message, ContentBlock } from "../../store/aiChatStore";
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

function renderBlocks(blocks: ContentBlock[], streaming?: boolean) {
  return blocks.map((block, idx) => {
    if (block.type === "text") {
      return (
        <div key={`text-${idx}`} style={assistantBubble}>
          {block.text}
          {streaming && idx === blocks.length - 1 && (
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
      );
    }
    // Tool block
    return (
      <ToolCallBlock key={block.toolCall.id || `tool-${idx}`} toolCall={block.toolCall} />
    );
  });
}

export function ChatMessage({ message, canUndo, onUndo }: ChatMessageProps) {
  const isUser = message.role === "user";
  const blocks = message.contentBlocks;
  const hasBlocks = blocks && blocks.length > 0;

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
      <div style={{ minWidth: 0, maxWidth: "85%", display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
        {/* Interleaved content blocks (assistant with blocks) */}
        {!isUser && hasBlocks ? (
          renderBlocks(blocks, message.streaming)
        ) : (
          /* Fallback: single bubble (user messages, or assistant without blocks) */
          <div style={isUser ? userBubble : assistantBubble}>
            {message.content}
            {message.streaming && !hasBlocks && (
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
        )}

        {/* Token count + undo (after streaming completes) */}
        {!isUser && !message.streaming && (
          <div
            style={{
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
