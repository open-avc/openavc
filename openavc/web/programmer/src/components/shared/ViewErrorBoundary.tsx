import { Component, type ReactNode, type ErrorInfo } from "react";

interface Props {
  viewName: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
  componentStack: string | null;
}

/**
 * Per-view error boundary. A crash in one view doesn't take down the entire app.
 * Shows a recovery UI with retry and navigate-away options.
 */
export class ViewErrorBoundary extends Component<Props, State> {
  state: State = { error: null, componentStack: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[ViewErrorBoundary:${this.props.viewName}]`, error, info.componentStack);
    this.setState({ componentStack: info.componentStack ?? null });
  }

  componentDidUpdate(prevProps: Props) {
    // Reset error when navigating to a different view
    if (prevProps.viewName !== this.props.viewName && this.state.error) {
      this.setState({ error: null, componentStack: null });
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
            padding: "var(--space-xl)",
            textAlign: "center",
            gap: "var(--space-md)",
          }}
        >
          <div
            style={{
              maxWidth: 480,
              padding: "var(--space-xl)",
              background: "var(--bg-surface)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
            }}
          >
            <h2
              style={{
                fontSize: "var(--font-size-lg)",
                color: "var(--color-error, #ef4444)",
                marginBottom: "var(--space-md)",
              }}
            >
              This view crashed
            </h2>
            <p
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-lg)",
                lineHeight: 1.5,
              }}
            >
              An error occurred in <strong>{this.props.viewName}</strong>.
              The rest of the application is still working. You can try again
              or switch to a different tab.
            </p>
            <details
              style={{
                marginBottom: "var(--space-lg)",
                fontSize: 12,
                color: "var(--text-muted)",
                textAlign: "left",
              }}
            >
              <summary style={{ cursor: "pointer", marginBottom: "var(--space-sm)" }}>
                Technical details
              </summary>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  padding: "var(--space-sm)",
                  background: "var(--bg-base)",
                  borderRadius: "var(--border-radius)",
                  overflow: "auto",
                  maxHeight: 200,
                }}
              >
                {this.state.error.message}
                {this.state.componentStack && `\n${this.state.componentStack}`}
              </pre>
            </details>
            <div style={{ display: "flex", gap: "var(--space-sm)", justifyContent: "center" }}>
              <button
                onClick={() => this.setState({ error: null, componentStack: null })}
                style={{
                  padding: "var(--space-sm) var(--space-lg)",
                  background: "var(--accent-bg)",
                  color: "var(--text-on-accent)",
                  border: "none",
                  borderRadius: "var(--border-radius)",
                  cursor: "pointer",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                Try Again
              </button>
              <button
                onClick={() => window.location.reload()}
                style={{
                  padding: "var(--space-sm) var(--space-lg)",
                  background: "var(--bg-hover)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--border-radius)",
                  cursor: "pointer",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                Reload Page
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
