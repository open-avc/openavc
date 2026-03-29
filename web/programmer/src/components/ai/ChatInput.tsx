import { useState, useCallback } from "react";
import { Send } from "lucide-react";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function ChatInput({ onSend, disabled, placeholder }: ChatInputProps) {
  const [text, setText] = useState("");

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

  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-sm)",
        padding: "var(--space-md)",
        borderTop: "1px solid var(--border-color)",
        background: "var(--bg-primary)",
      }}
    >
      <textarea
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
          minHeight: 36,
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
        }}
      >
        <Send size={16} />
      </button>
    </div>
  );
}
