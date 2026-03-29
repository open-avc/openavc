import { useToastStore } from "../../store/toastStore";
import type { Toast } from "../../store/toastStore";

const severityColors: Record<Toast["severity"], string> = {
  info: "#2196F3",
  success: "#4CAF50",
  warning: "#FF9800",
  error: "#F44336",
};

export default function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts);
  const removeToast = useToastStore((s) => s.removeToast);

  if (toasts.length === 0) return null;

  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      style={{
        position: "fixed",
        bottom: 16,
        right: 16,
        zIndex: 10000,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        maxWidth: 420,
      }}
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role="alert"
          style={{
            background: "#2a2a2a",
            borderLeft: `4px solid ${severityColors[t.severity]}`,
            color: "#eee",
            padding: "10px 14px",
            borderRadius: 6,
            fontSize: 13,
            boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            animation: "toast-in 0.2s ease-out",
          }}
        >
          <span style={{ flex: 1, wordBreak: "break-word" }}>{t.message}</span>
          <button
            onClick={() => removeToast(t.id)}
            style={{
              background: "none",
              border: "none",
              color: "#999",
              cursor: "pointer",
              fontSize: 16,
              lineHeight: 1,
              padding: 0,
              flexShrink: 0,
            }}
            aria-label="Dismiss"
          >
            &times;
          </button>
        </div>
      ))}
    </div>
  );
}
