/**
 * Reusable copy-to-clipboard button with visual feedback.
 */
import { useState } from "react";
import { Copy, Check } from "lucide-react";
import { copyToClipboard } from "./clipboard";

interface CopyButtonProps {
  value: string;
  size?: number;
  title?: string;
}

export function CopyButton({ value, size = 12, title = "Copy to clipboard" }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!(await copyToClipboard(value))) return;
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      style={btnStyle}
      title={title}
      aria-label={title}
    >
      {copied ? <Check size={size} style={{ color: "var(--accent)" }} /> : <Copy size={size} />}
      {copied && (
        <span style={copiedLabelStyle}>Copied!</span>
      )}
    </button>
  );
}

const btnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  padding: 2,
  background: "none",
  border: "none",
  color: "var(--text-muted)",
  cursor: "pointer",
  opacity: 0.5,
  flexShrink: 0,
  position: "relative",
};

const copiedLabelStyle: React.CSSProperties = {
  position: "absolute",
  top: -18,
  right: 0,
  fontSize: 10,
  color: "var(--accent)",
  whiteSpace: "nowrap",
};
