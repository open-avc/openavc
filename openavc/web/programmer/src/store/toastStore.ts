import { create } from "zustand";

export interface Toast {
  id: string;
  message: string;
  severity: "info" | "success" | "warning" | "error";
  duration: number; // ms, 0 = persistent
}

interface ToastState {
  toasts: Toast[];
  /** Shows a toast and returns its id, for `removeToast`. */
  addToast: (message: string, severity?: Toast["severity"], duration?: number) => string;
  removeToast: (id: string) => void;
}

let nextId = 0;

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  addToast: (message, severity = "error", duration = 5000) => {
    const id = String(++nextId);
    set((s) => ({ toasts: [...s.toasts, { id, message, severity, duration }] }));
    if (duration > 0) {
      setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
      }, duration);
    }
    return id;
  },
  removeToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Shorthand for showing an error toast (replaces alert()). 10s to allow reading. */
export function showError(message: string): void {
  useToastStore.getState().addToast(message, "error", 10000);
}

/** Shorthand for showing an info toast; `duration` (ms) for a longer one. */
export function showInfo(message: string, duration = 3000): void {
  useToastStore.getState().addToast(message, "info", duration);
}

/** Shorthand for showing a success toast; `duration` (ms) for a longer one. */
export function showSuccess(message: string, duration = 3000): void {
  useToastStore.getState().addToast(message, "success", duration);
}

/** A warning that stays until it is dismissed: something the reader has to
 *  go and do, such as a list of devices to open. */
export function showLastingWarning(message: string): void {
  useToastStore.getState().addToast(message, "warning", 0);
}
