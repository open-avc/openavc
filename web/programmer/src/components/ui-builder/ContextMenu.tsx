import { useEffect, useRef } from "react";
import { Copy, Clipboard, Trash2, CopyPlus, ArrowUpToLine, ArrowDownToLine, Globe, Undo2 } from "lucide-react";

interface ContextMenuProps {
  x: number;
  y: number;
  elementId: string;
  isMaster?: boolean;
  multiSelectCount?: number;
  onClose: () => void;
  onDuplicate: (elementId: string) => void;
  onDelete: (elementId: string) => void;
  onDeleteAll?: () => void;
  onDuplicateAll?: () => void;
  onCopy: (elementIds: string[]) => void;
  onPaste: () => void;
  onBringToFront: (elementId: string) => void;
  onSendToBack: (elementId: string) => void;
  onPromoteToMaster?: (elementId: string) => void;
  onDemoteFromMaster?: (elementId: string) => void;
  onDeleteMaster?: (elementId: string) => void;
  hasClipboard: boolean;
}

export function ContextMenu({
  x,
  y,
  elementId,
  isMaster,
  multiSelectCount,
  onClose,
  onDuplicate,
  onDelete,
  onDeleteAll,
  onDuplicateAll,
  onCopy,
  onPaste,
  onBringToFront,
  onSendToBack,
  onPromoteToMaster,
  onDemoteFromMaster,
  onDeleteMaster,
  hasClipboard,
}: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  // Clamp menu position to viewport on mount
  useEffect(() => {
    if (ref.current) {
      const rect = ref.current.getBoundingClientRect();
      if (rect.right > window.innerWidth) {
        ref.current.style.left = `${Math.max(0, window.innerWidth - rect.width - 8)}px`;
      }
      if (rect.bottom > window.innerHeight) {
        ref.current.style.top = `${Math.max(0, window.innerHeight - rect.height - 8)}px`;
      }
    }
  }, [x, y]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [onClose]);

  const items: {
    label: string;
    icon: React.ReactNode;
    shortcut?: string;
    onClick: () => void;
    disabled?: boolean;
    danger?: boolean;
    separator?: boolean;
  }[] = isMaster
    ? [
        {
          label: "Move to Page",
          icon: <Undo2 size={14} />,
          onClick: () => { onDemoteFromMaster?.(elementId); onClose(); },
        },
        {
          label: "Delete Master",
          icon: <Trash2 size={14} />,
          onClick: () => { onDeleteMaster?.(elementId); onClose(); },
          danger: true,
        },
      ]
    : multiSelectCount && multiSelectCount > 1
    ? [
        {
          label: `Duplicate All (${multiSelectCount})`,
          icon: <CopyPlus size={14} />,
          onClick: () => { onDuplicateAll?.(); onClose(); },
        },
        {
          label: `Copy All (${multiSelectCount})`,
          icon: <Copy size={14} />,
          shortcut: "Ctrl+C",
          onClick: () => { onCopy([elementId]); onClose(); },
        },
        {
          label: "Paste",
          icon: <Clipboard size={14} />,
          shortcut: "Ctrl+V",
          onClick: () => { onPaste(); onClose(); },
          disabled: !hasClipboard,
        },
        {
          label: `Delete All (${multiSelectCount})`,
          icon: <Trash2 size={14} />,
          shortcut: "Del",
          onClick: () => { onDeleteAll?.(); onClose(); },
          danger: true,
        },
      ]
    : [
        {
          label: "Duplicate",
          icon: <CopyPlus size={14} />,
          shortcut: "Ctrl+D",
          onClick: () => { onDuplicate(elementId); onClose(); },
        },
        {
          label: "Copy",
          icon: <Copy size={14} />,
          shortcut: "Ctrl+C",
          onClick: () => { onCopy([elementId]); onClose(); },
        },
        {
          label: "Paste",
          icon: <Clipboard size={14} />,
          shortcut: "Ctrl+V",
          onClick: () => { onPaste(); onClose(); },
          disabled: !hasClipboard,
        },
        {
          label: "Bring to Front",
          icon: <ArrowUpToLine size={14} />,
          onClick: () => { onBringToFront(elementId); onClose(); },
        },
        {
          label: "Send to Back",
          icon: <ArrowDownToLine size={14} />,
          onClick: () => { onSendToBack(elementId); onClose(); },
        },
        {
          label: "Make Master Element",
          icon: <Globe size={14} />,
          onClick: () => { onPromoteToMaster?.(elementId); onClose(); },
          separator: true,
        },
        {
          label: "Delete",
          icon: <Trash2 size={14} />,
          shortcut: "Del",
          onClick: () => { onDelete(elementId); onClose(); },
          danger: true,
        },
      ];

  return (
    <div
      ref={ref}
      style={{
        position: "fixed",
        left: x,
        top: y,
        zIndex: 1000,
        background: "var(--bg-elevated)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        boxShadow: "var(--shadow-lg)",
        padding: "var(--space-xs)",
        minWidth: 180,
      }}
    >
      {items.map((item, i) => (
        <div key={i}>
        {item.separator && (
          <div style={{ borderTop: "1px solid var(--border-color)", margin: "4px 0" }} />
        )}
        <button
          onClick={item.onClick}
          disabled={item.disabled}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            width: "100%",
            padding: "6px 10px",
            borderRadius: 4,
            fontSize: "var(--font-size-sm)",
            color: item.danger
              ? "var(--color-error)"
              : item.disabled
                ? "var(--text-muted)"
                : "var(--text-primary)",
            textAlign: "left",
            cursor: item.disabled ? "default" : "pointer",
            opacity: item.disabled ? 0.5 : 1,
          }}
          onMouseEnter={(e) => {
            if (!item.disabled) {
              (e.currentTarget as HTMLElement).style.background =
                "var(--bg-hover)";
            }
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.background = "transparent";
          }}
        >
          <span style={{ display: "flex", opacity: 0.7 }}>{item.icon}</span>
          <span style={{ flex: 1 }}>{item.label}</span>
          {item.shortcut && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {item.shortcut}
            </span>
          )}
        </button>
        </div>
      ))}
    </div>
  );
}
