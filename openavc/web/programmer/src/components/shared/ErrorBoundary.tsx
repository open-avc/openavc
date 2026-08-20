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
            padding: "var(--space-2xl)",
            color: "var(--text-primary)",
            fontFamily: "var(--font-family)",
            fontSize: "var(--font-size-lg)",
          }}
        >
          <h2 style={{ marginBottom: "var(--space-sm)" }}>Something went wrong</h2>
          <p style={{ opacity: 0.7, marginBottom: "var(--space-lg)" }}>
            An unexpected error occurred. Try refreshing the page or clicking the button below.
          </p>
          <details style={{ marginBottom: "var(--space-lg)", fontSize: "var(--font-size-sm)", opacity: 0.6 }}>
            <summary style={{ cursor: "pointer" }}>Technical details</summary>
            <pre style={{ whiteSpace: "pre-wrap", marginTop: "var(--space-sm)", fontFamily: "monospace", fontSize: "var(--font-size-xs)" }}>
              {this.state.error.message}
            </pre>
            {this.state.componentStack && (
              <pre style={{ whiteSpace: "pre-wrap", opacity: 0.5, marginTop: "var(--space-xs)", fontSize: "var(--font-size-2xs)" }}>
                {this.state.componentStack}
              </pre>
            )}
          </details>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <button
              onClick={() => this.setState({ error: null, componentStack: null })}
              style={{
                padding: "var(--space-sm) var(--space-lg)",
                background: "#333",
                color: "var(--text-on-accent)",
                border: "none",
                borderRadius: "var(--border-radius)",
                cursor: "pointer",
              }}
            >
              Try Again
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{
                padding: "var(--space-sm) var(--space-lg)",
                background: "transparent",
                color: "var(--text-primary)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
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
