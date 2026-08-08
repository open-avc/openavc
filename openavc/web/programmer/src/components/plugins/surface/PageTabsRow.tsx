/**
 * Emergent pages: the tab row, the add button, and the per-page menu.
 *
 * Pages are not declared anywhere — a page exists because something references
 * it — so this row is also where one is created, renamed, duplicated, cleared
 * and (when nothing keeps it alive) deleted.
 */
import { useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { pageMenuConfirmStyle } from "./styles";

export function PageTabsRow({
  totalPages,
  pageCount,
  currentPage,
  pageLabel,
  onSelect,
  onAdd,
  onRename,
  onDuplicate,
  onClearPage,
  canDelete,
  onDelete,
  hasContent,
}: {
  totalPages: number;
  pageCount: number;
  currentPage: number;
  pageLabel: (p: number) => string;
  onSelect: (p: number) => void;
  onAdd: () => void;
  onRename: (p: number, name: string) => void;
  onDuplicate: () => void;
  onClearPage: () => void;
  canDelete: boolean;
  onDelete: () => void;
  hasContent: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const commit = () => {
    setEditing(false);
    onRename(currentPage, draft);
  };
  const startRename = () => {
    const label = pageLabel(currentPage);
    setDraft(label !== `Page ${currentPage + 1}` ? label : "");
    setEditing(true);
    setMenuOpen(false);
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", flexWrap: "wrap" }}>
      {Array.from({ length: totalPages }, (_, p) => {
        const isActive = p === currentPage;
        const isDraft = p >= pageCount;
        if (isActive && editing) {
          return (
            <input
              key={p}
              autoFocus
              value={draft}
              placeholder={`Page ${p + 1}`}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") setEditing(false);
              }}
              style={{
                width: 110,
                padding: "3px 8px",
                borderRadius: "var(--border-radius)",
                border: "1px solid var(--accent)",
                background: "var(--bg-surface)",
                color: "var(--text-primary)",
                fontSize: "var(--font-size-sm)",
              }}
            />
          );
        }
        return (
          <button
            key={p}
            onClick={() => onSelect(p)}
            onDoubleClick={isActive ? startRename : undefined}
            title={isActive ? "Double-click to rename this page" : undefined}
            style={{
              padding: "3px 12px",
              borderRadius: "var(--border-radius)",
              border: isActive ? "1px solid var(--accent)" : "1px solid var(--border-color)",
              background: isActive ? "var(--accent-dim)" : "var(--bg-surface)",
              color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
              fontSize: "var(--font-size-sm)",
              fontWeight: isActive ? 600 : 400,
              fontStyle: isDraft ? "italic" : "normal",
              opacity: isDraft && !isActive ? 0.6 : 1,
              cursor: "pointer",
              maxWidth: 160,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {pageLabel(p)}
          </button>
        );
      })}
      <button
        onClick={onAdd}
        title="Add a page"
        style={{
          padding: "3px 10px",
          borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)",
          background: "transparent",
          color: "var(--text-muted)",
          fontSize: "var(--font-size-sm)",
          cursor: "pointer",
        }}
      >
        +
      </button>
      <div style={{ position: "relative" }}>
        <button
          onClick={() => {
            setMenuOpen(!menuOpen);
            setConfirmClear(false);
            setConfirmDelete(false);
          }}
          title="Page actions"
          style={{
            display: "flex",
            alignItems: "center",
            padding: "4px 6px",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            color: "var(--text-secondary)",
            cursor: "pointer",
          }}
        >
          <MoreHorizontal size={14} />
        </button>
        {menuOpen && (
          <div
            style={{
              position: "absolute",
              top: "100%",
              left: 0,
              zIndex: 50,
              marginTop: 4,
              minWidth: 210,
              background: "var(--bg-surface)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <button onClick={startRename} style={pageMenuItemStyle(true)}>
              Rename page
            </button>
            <button
              onClick={() => {
                onDuplicate();
                setMenuOpen(false);
              }}
              disabled={!hasContent}
              title={hasContent ? "Copy this page's keys onto a new page" : "Nothing on this page to copy"}
              style={pageMenuItemStyle(hasContent)}
            >
              Duplicate to a new page
            </button>
            {!confirmClear ? (
              <button
                onClick={() => setConfirmClear(true)}
                disabled={!hasContent}
                style={{ ...pageMenuItemStyle(hasContent), color: hasContent ? "var(--color-error)" : undefined }}
              >
                Clear this page...
              </button>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", padding: "var(--space-sm) var(--space-md)", fontSize: 12 }}>
                <span style={{ color: "var(--color-error)" }}>Remove every key?</span>
                <button
                  onClick={() => {
                    onClearPage();
                    setMenuOpen(false);
                    setConfirmClear(false);
                  }}
                  style={pageMenuConfirmStyle}
                >
                  Yes
                </button>
                <button onClick={() => setConfirmClear(false)} style={pageMenuConfirmStyle}>
                  No
                </button>
              </div>
            )}
            {!confirmDelete ? (
              <button
                onClick={() => setConfirmDelete(true)}
                disabled={!canDelete}
                title={
                  canDelete
                    ? "Remove the last page (its keys and name go with it)"
                    : "Only the last page can be deleted, and only when no rule or page key still points at it"
                }
                style={{ ...pageMenuItemStyle(canDelete), color: canDelete ? "var(--color-error)" : undefined }}
              >
                Delete page
              </button>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", padding: "var(--space-sm) var(--space-md)", fontSize: 12 }}>
                <span style={{ color: "var(--color-error)" }}>Delete this page?</span>
                <button
                  onClick={() => {
                    onDelete();
                    setMenuOpen(false);
                    setConfirmDelete(false);
                  }}
                  style={pageMenuConfirmStyle}
                >
                  Yes
                </button>
                <button onClick={() => setConfirmDelete(false)} style={pageMenuConfirmStyle}>
                  No
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const pageMenuItemStyle = (enabled: boolean): React.CSSProperties => ({
  padding: "var(--space-sm) var(--space-md)",
  textAlign: "left",
  fontSize: "var(--font-size-sm)",
  cursor: enabled ? "pointer" : "default",
  opacity: enabled ? 1 : 0.45,
  background: "transparent",
});
