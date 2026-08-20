import { Component, type ReactNode, type ErrorInfo } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  componentStack: string | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, componentStack: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
    this.setState({ componentStack: info.componentStack ?? null });
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            padding: 32,
            color: "var(--text-primary, #f44336)",
            fontFamily: "var(--font-sans, sans-serif)",
            fontSize: 14,
          }}
        >
          <h2 style={{ marginBottom: 8 }}>Something went wrong</h2>
          <p style={{ opacity: 0.7, marginBottom: 16 }}>
            An unexpected error occurred. Try refreshing the page or clicking the button below.
          </p>
          <details style={{ marginBottom: 16, fontSize: 12, opacity: 0.6 }}>
            <summary style={{ cursor: "pointer" }}>Technical details</summary>
            <pre style={{ whiteSpace: "pre-wrap", marginTop: 8, fontFamily: "monospace", fontSize: 11 }}>
              {this.state.error.message}
            </pre>
            {this.state.componentStack && (
              <pre style={{ whiteSpace: "pre-wrap", opacity: 0.5, marginTop: 4, fontSize: 10 }}>
                {this.state.componentStack}
              </pre>
            )}
          </details>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={() => this.setState({ error: null, componentStack: null })}
              style={{
                padding: "8px 16px",
                background: "#333",
                color: "#fff",
                border: "none",
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              Try Again
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{
                padding: "8px 16px",
                background: "transparent",
                color: "var(--text-primary, #fff)",
                border: "1px solid var(--border-color, #555)",
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              Reload Page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
