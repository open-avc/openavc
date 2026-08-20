import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";

interface CollapsibleSectionProps {
  title: string;
  /** Small right-aligned status hint shown in the header (e.g. "5 commands", "disabled"). */
  meta?: string;
  /** One-line description shown beneath the title in the header bar. */
  subtitle?: string;
  /** Whether the section is open on first render. Default true. */
  defaultOpen?: boolean;
  /** Optional "Learn more" link rendered next to the meta hint. */
  helpHref?: string;
  children: ReactNode;
}

/**
 * Consistent collapsible wrapper used for sub-sections within a Driver Builder
 * tab. Header is always visible and clickable; body renders only when open so
 * heavy editors don't pay layout cost while collapsed.
 */
export function CollapsibleSection({
  title,
  meta,
  subtitle,
  defaultOpen = true,
  helpHref,
  children,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "var(--bg-base)",
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          width: "100%",
          padding: "var(--space-sm) var(--space-md)",
          background: open ? "var(--bg-surface)" : "transparent",
          borderBottom: open ? "1px solid var(--border-color)" : "none",
          textAlign: "left",
          cursor: "pointer",
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <span
            style={{
              fontSize: "var(--font-size-md)",
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            {title}
          </span>
          {subtitle && (
            <span
              style={{
                fontSize: "11px",
                color: "var(--text-muted)",
                fontWeight: 400,
              }}
            >
              {subtitle}
            </span>
          )}
        </span>
        {meta && (
          <span
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              flexShrink: 0,
            }}
          >
            {meta}
          </span>
        )}
        {helpHref && (
          <a
            href={helpHref}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            title="Open documentation in a new tab"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 3,
              fontSize: "11px",
              color: "var(--text-muted)",
              textDecoration: "none",
              flexShrink: 0,
              padding: "2px 6px",
              borderRadius: "var(--border-radius)",
            }}
          >
            <ExternalLink size={11} /> Learn more
          </a>
        )}
      </button>
      {open && (
        <div style={{ padding: "var(--space-md) var(--space-lg)" }}>
          {children}
        </div>
      )}
    </section>
  );
}
