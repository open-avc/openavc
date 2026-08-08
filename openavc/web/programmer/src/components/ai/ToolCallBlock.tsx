import { useState } from "react";
import { ChevronDown, ChevronRight, Wrench, Check, X, Loader2 } from "lucide-react";
import type { LiveToolCall } from "../../store/aiChatStore";

interface ToolCallBlockProps {
  toolCall:
    | { id: string; name: string; input: Record<string, unknown> }
    | LiveToolCall;
}

export function ToolCallBlock({ toolCall }: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false);

  // Determine if this is a live (streaming) tool call or a static one
  const isLive = "status" in toolCall;
  const status = isLive ? toolCall.status : "success";
  const summary = isLive && "summary" in toolCall ? toolCall.summary : undefined;
  const durationMs = isLive && "durationMs" in toolCall ? toolCall.durationMs : undefined;
  const input = toolCall.input;

  const statusIcon =
    status === "running" ? (
      <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
    ) : status === "error" ? (
      <X size={12} />
    ) : (
      <Check size={12} />
    );

  const statusColor =
    status === "running"
      ? "var(--text-secondary)"
      : status === "error"
        ? "var(--status-error, #f44336)"
        : "var(--status-success, #4caf50)";

  return (
    <div
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        fontSize: "var(--font-size-sm)",
        marginTop: "var(--space-xs)",
        maxWidth: "85%",
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-xs) var(--space-sm)",
          background: "none",
          border: "none",
          color: "var(--text-secondary)",
          cursor: "pointer",
          width: "100%",
          textAlign: "left",
          fontSize: "var(--font-size-sm)",
        }}
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span style={{ color: statusColor, display: "flex", alignItems: "center" }}>
          {statusIcon}
        </span>
        <Wrench size={12} />
        <span style={{ fontWeight: 500 }}>{toolCall.name}</span>
        {summary && (
          <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
            {" "}
            {summary}
          </span>
        )}
        {durationMs != null && (
          <span
            style={{
              marginLeft: "auto",
              color: "var(--text-muted)",
              fontSize: 10,
              flexShrink: 0,
            }}
          >
            {durationMs}ms
          </span>
        )}
      </button>
      {expanded && input && (
        <pre
          style={{
            padding: "var(--space-sm)",
            borderTop: "1px solid var(--border-color)",
            margin: 0,
            fontSize: 11,
            color: "var(--text-muted)",
            overflow: "auto",
            maxHeight: 200,
          }}
        >
          {JSON.stringify(input, null, 2)}
        </pre>
      )}
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
