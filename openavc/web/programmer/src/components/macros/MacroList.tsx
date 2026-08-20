import { useState } from "react";
import { Plus, Trash2, AlertTriangle } from "lucide-react";
import type { MacroConfig } from "../../api/types";
import { CopyButton } from "../shared/CopyButton";
import { issueLabel, issueSummary, type MacroIssuesById } from "./macroLint";

interface MacroListProps {
  macros: MacroConfig[];
  /** What will not run, by macro id. The mark belongs here and not only in
   *  the open editor: a macro that saved cleanly is one nobody reopens. */
  issues?: MacroIssuesById;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onDelete: (id: string) => void;
}

export function MacroList({ macros, issues, selectedId, onSelect, onAdd, onDelete }: MacroListProps) {
  const [search, setSearch] = useState("");
  const filtered = macros.filter(m => !search || m.name.toLowerCase().includes(search.toLowerCase()));

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        borderRight: "1px solid var(--border-color)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--space-sm) var(--space-md)",
          borderBottom: "1px solid var(--border-color)",
        }}
      >
        <span
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "var(--tracking-wide)",
            fontWeight: "var(--font-weight-semibold)",
          }}
        >
          Macros ({macros.length})
        </span>
        <button
          onClick={onAdd}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-2xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            background: "var(--accent-bg)",
            color: "#fff",
            fontSize: "var(--font-size-sm)",
            border: "none",
            cursor: "pointer",
          }}
          title="Add macro"
        >
          <Plus size={14} />
        </button>
      </div>

      <div style={{ padding: "var(--space-xs) var(--space-sm)", borderBottom: "1px solid var(--border-color)" }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search macros..."
          style={{
            width: "100%",
            padding: "var(--space-xs) var(--space-sm)",
            fontSize: "var(--font-size-sm)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)",
            color: "var(--text-primary)",
          }}
        />
      </div>

      <ul style={{ flex: 1, overflow: "auto", margin: 0, padding: 0, listStyle: "none" }} role="listbox" aria-label="Macros">
        {filtered.length === 0 ? (
          <div
            style={{
              padding: "var(--space-lg)",
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
              lineHeight: "var(--line-base)",
            }}
          >
            {macros.length === 0 ? (
              <>
                No macros yet.
                <br />
                Click <strong>+</strong> to create one.
                <br /><br />
                <a href="https://docs.openavc.com/macros-and-triggers" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
                  Learn about macros and triggers
                </a>
              </>
            ) : (
              <>No macros match "{search}".</>
            )}
          </div>
        ) : (
          filtered.map((m) => (
            <li
              key={m.id}
              role="option"
              aria-selected={selectedId === m.id}
              onClick={() => onSelect(m.id)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "var(--space-sm) var(--space-md)",
                cursor: "pointer",
                background:
                  selectedId === m.id
                    ? "var(--accent-dim)"
                    : "transparent",
                borderBottom: "1px solid var(--border-color)",
              }}
              onMouseEnter={(e) => {
                if (selectedId !== m.id)
                  (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
              }}
              onMouseLeave={(e) => {
                if (selectedId !== m.id)
                  (e.currentTarget as HTMLElement).style.background = "transparent";
              }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div
                  style={{
                    fontSize: "var(--font-size-sm)",
                    color: "var(--text-primary)",
                    fontWeight: selectedId === m.id ? 600 : 400,
                  }}
                >
                  {m.name}
                </div>
                <div
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--text-muted)",
                  }}
                >
                  {m.steps.length} step{m.steps.length !== 1 ? "s" : ""}
                </div>
                {(issues?.[m.id]?.length ?? 0) > 0 && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-xs)",
                      marginTop: "var(--space-2xs)",
                      fontSize: "var(--font-size-xs)",
                      color: "#f59e0b",
                    }}
                    title={issues![m.id]
                      .map((i) => `${issueLabel(i)}: ${i.message}`)
                      .join("\n")}
                  >
                    <AlertTriangle size={11} style={{ flexShrink: 0 }} />
                    {issueSummary(issues![m.id])} won't run
                  </div>
                )}
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2xs)", marginTop: "var(--space-2xs)" }}>
                  <code style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", fontFamily: "var(--font-mono)", opacity: 0.7, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {m.id}
                  </code>
                  <CopyButton value={m.id} size={10} title="Copy macro ID" />
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(m.id);
                }}
                style={{
                  display: "flex",
                  padding: "var(--space-xs)",
                  borderRadius: "var(--border-radius)",
                  background: "transparent",
                  color: "var(--text-muted)",
                  border: "none",
                  cursor: "pointer",
                }}
                title="Delete macro"
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
