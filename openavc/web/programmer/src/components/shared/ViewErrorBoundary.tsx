import { Component, type ReactNode, type ErrorInfo } from "react";

interface Props {
  viewName: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
  componentStack: string | null;
  staleBundle: boolean;
}

/** How a browser says a lazily-loaded chunk is no longer on the server. Each
 *  engine words it differently, and Vite's CSS preloader has its own. */
const STALE_BUNDLE_SHAPES = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /unable to preload css/i,
  /loading chunk \S+ failed/i,
  /expected a javascript(-or-wasm)? module script/i,
];

function isStaleBundle(error: Error | null): boolean {
  const message = error?.message ?? "";
  return STALE_BUNDLE_SHAPES.some((shape) => shape.test(message));
}

const RELOAD_MARK = "openavc.staleBundleReload";
/** Long enough that a reload which did not fix it cannot become a loop, short
 *  enough that the next rebuild days or minutes later still recovers itself. */
const RELOAD_GUARD_MS = 60_000;

/** True when this tab may reload itself now. False when it just did and the
 *  chunk is still missing, and false when there is nowhere to record the
 *  attempt — an unguarded reload is worse than the card. */
function claimReload(): boolean {
  try {
    const store = window.sessionStorage;
    const last = Number(store.getItem(RELOAD_MARK) || 0);
    if (Date.now() - last < RELOAD_GUARD_MS) return false;
    store.setItem(RELOAD_MARK, String(Date.now()));
    return true;
  } catch {
    return false;
  }
}

/**
 * Per-view error boundary. A crash in one view doesn't take down the entire app.
 * Shows a recovery UI with retry and navigate-away options.
 */
export class ViewErrorBoundary extends Component<Props, State> {
  state: State = { error: null, componentStack: null, staleBundle: false };

  static getDerivedStateFromError(error: Error) {
    // Decided here rather than in componentDidCatch so the crash card never
    // flashes on the way to the reload.
    return { error, staleBundle: isStaleBundle(error) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[ViewErrorBoundary:${this.props.viewName}]`, error, info.componentStack);
    this.setState({ componentStack: info.componentStack ?? null });
    if (isStaleBundle(error) && claimReload()) window.location.reload();
  }

  componentDidUpdate(prevProps: Props) {
    // Reset error when navigating to a different view
    if (prevProps.viewName !== this.props.viewName && this.state.error) {
      this.setState({ error: null, componentStack: null, staleBundle: false });
    }
  }

  render() {
    if (this.state.error && this.state.staleBundle) {
      // Reached when the reload could not be taken: one has just happened and
      // did not help, storage is blocked, or the browser asked about unsaved
      // changes (App's beforeunload guard) and the answer was no. The chunk
      // named in the crash means nothing to a reader, so it is not shown.
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
                marginBottom: "var(--space-md)",
              }}
            >
              The app was updated
            </h2>
            <p
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-lg)",
                lineHeight: 1.5,
              }}
            >
              This tab is still running the version it loaded, and the files
              that version needs are no longer on the server. Reload to pick up
              the new one. You will be asked first if anything is unsaved.
            </p>
            <button
              onClick={() => window.location.reload()}
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
              Reload Page
            </button>
          </div>
        </div>
      );
    }
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
