import { create } from "zustand";

export interface Toast {
  id: string;
  message: string;
  severity: "info" | "success" | "warning" | "error";
  duration: number; // ms, 0 = persistent
}

interface ToastState {
  toasts: Toast[];
  addToast: (message: string, severity?: Toast["severity"], duration?: number) => void;
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
  },
  removeToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Shorthand for showing an error toast (replaces alert()). 10s to allow reading. */
export function showError(message: string): void {
  useToastStore.getState().addToast(message, "error", 10000);
}

/** Shorthand for showing an info toast. */
export function showInfo(message: string): void {
  useToastStore.getState().addToast(message, "info", 3000);
}

/** Shorthand for showing a success toast. */
export function showSuccess(message: string): void {
  useToastStore.getState().addToast(message, "success", 3000);
}
