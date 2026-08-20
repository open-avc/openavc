import { useState } from "react";
import { Bot, User, Undo2, AlertCircle, RotateCw, Copy, Check } from "lucide-react";
import type { Message, ContentBlock } from "../../store/aiChatStore";
import { ToolCallBlock } from "./ToolCallBlock";
import { MarkdownContent } from "./MarkdownContent";
import { copyToClipboard } from "../shared/clipboard";

interface ChatMessageProps {
  message: Message;
  canUndo?: boolean;
  onUndo?: () => void;
  /** Send this message again. Only passed for one that failed to send. */
  onRetry?: () => void;
}

const bubbleBase: React.CSSProperties = {
  padding: "var(--space-md)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  lineHeight: "var(--line-relaxed)",
  maxWidth: "85%",
  wordBreak: "break-word",
};

const userBubble: React.CSSProperties = {
  ...bubbleBase,
  whiteSpace: "pre-wrap",
  background: "var(--accent-bg)",
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
      const isLast = idx === blocks.length - 1;
      return (
        <div key={`text-${idx}`} style={assistantBubble}>
          <MarkdownContent
            content={block.text}
            streaming={streaming && isLast}
          />
        </div>
      );
    }
    // Tool block
    return (
      <ToolCallBlock key={block.toolCall.id || `tool-${idx}`} toolCall={block.toolCall} />
    );
  });
}

const smallButton: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-2xs) var(--space-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-2xs)",
  cursor: "pointer",
};

/**
 * The footer on a message that never sent: what went wrong, and the two
 * things you'd want next — send it again, or take the text somewhere else.
 */
function FailedFooter({ message, onRetry }: { message: Message; onRetry?: () => void }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!(await copyToClipboard(message.content))) return;
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
        flexWrap: "wrap",
        gap: "var(--space-sm)",
      }}
    >
      <span
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-2xs)",
          color: "var(--color-error)",
          textAlign: "right",
        }}
      >
        <AlertCircle size={10} style={{ flexShrink: 0 }} />
        Not sent. {message.failed}
      </span>
      {onRetry && (
        <button onClick={onRetry} style={smallButton} title="Send this message again">
          <RotateCw size={10} /> Retry
        </button>
      )}
      <button onClick={handleCopy} style={smallButton} title="Copy this message">
        {copied ? <Check size={10} /> : <Copy size={10} />} {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function ChatMessage({ message, canUndo, onUndo, onRetry }: ChatMessageProps) {
  const isUser = message.role === "user";
  const blocks = message.contentBlocks;
  const hasBlocks = blocks && blocks.length > 0;
  const failed = Boolean(message.failed);

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
          background: isUser ? "var(--accent-bg)" : "var(--bg-hover)",
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
          <div
            style={
              failed
                ? { ...userBubble, opacity: 0.75, border: "1px solid var(--color-error)" }
                : isUser
                  ? userBubble
                  : assistantBubble
            }
          >
            {isUser ? (
              message.content
            ) : (
              <MarkdownContent
                content={message.content}
                streaming={message.streaming && !hasBlocks}
              />
            )}
          </div>
        )}

        {/* Why it didn't send, and what to do about it */}
        {failed && <FailedFooter message={message} onRetry={onRetry} />}

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
            {Boolean(message.inputTokens || message.outputTokens) && (
              <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)" }}>
                {message.inputTokens?.toLocaleString()} in / {message.outputTokens?.toLocaleString()} out
              </span>
            )}
            {message.undoUnavailable && (
              <span
                style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", fontStyle: "italic" }}
                title="A project snapshot couldn't be captured before this response, so its changes can't be rolled back here."
              >
                Undo unavailable
              </span>
            )}
            {canUndo && onUndo && (
              <button
                onClick={onUndo}
                style={smallButton}
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
