import { useState, useCallback, useRef, useEffect } from "react";
import { Send } from "lucide-react";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function ChatInput({ onSend, disabled, placeholder }: ChatInputProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wasDisabled = useRef(disabled);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  }, [text, disabled, onSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  // Auto-resize textarea as content changes
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }, [text]);

  // Refocus textarea when sending completes (disabled goes from true to false)
  useEffect(() => {
    if (wasDisabled.current && !disabled) {
      textareaRef.current?.focus();
    }
    wasDisabled.current = disabled;
  }, [disabled]);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        gap: "var(--space-sm)",
        padding: "var(--space-md)",
        borderTop: "1px solid var(--border-color)",
        background: "var(--bg-primary)",
      }}
    >
      <textarea
        ref={textareaRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder || "Ask AI to build something..."}
        disabled={disabled}
        rows={1}
        style={{
          flex: 1,
          padding: "8px 12px",
          fontSize: "var(--font-size-sm)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          background: "var(--bg-surface)",
          color: "var(--text-primary)",
          resize: "none",
          fontFamily: "inherit",
          lineHeight: 1.5,
          minHeight: 38,
          maxHeight: 120,
          overflow: "auto",
        }}
      />
      <button
        onClick={handleSend}
        disabled={disabled || !text.trim()}
        style={{
          padding: "8px 12px",
          borderRadius: "var(--border-radius)",
          background: disabled || !text.trim() ? "var(--bg-hover)" : "var(--accent)",
          color: disabled || !text.trim() ? "var(--text-muted)" : "#fff",
          border: "none",
          cursor: disabled || !text.trim() ? "not-allowed" : "pointer",
          display: "flex",
          alignItems: "center",
          height: 38,
          flexShrink: 0,
        }}
      >
        <Send size={16} />
      </button>
    </div>
  );
}
