/**
 * The top of a plugin's page below its header: what the plugin is, its How to
 * Use notes (markdown from PLUGIN_INFO.usage), the error it stopped with, and
 * the actions it runs from its own page.
 */
import type { PluginInfo } from "../../api/types";
import { MarkdownContent } from "../ai/MarkdownContent";
import { ContextActionRenderer } from "./PluginExtensions";

export function PluginAbout({ info }: { info: PluginInfo }) {
  return (
    <>
      {info.description && (
        <p style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: "var(--space-lg)" }}>
          {info.description}
        </p>
      )}

      {info.usage && (
        <div
          style={{
            marginBottom: "var(--space-lg)",
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-color)",
          }}
        >
          <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: "var(--space-sm)", color: "var(--text-secondary)" }}>
            How to Use
          </h3>
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-primary)" }}>
            <MarkdownContent content={info.usage} />
          </div>
        </div>
      )}

      {info.status === "error" && info.error && (
        <div
          style={{
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            background: "rgba(244, 67, 54, 0.12)",
            border: "1px solid rgba(244, 67, 54, 0.3)",
            fontSize: "var(--font-size-sm)",
            color: "var(--color-error)",
          }}
        >
          <strong>Error:</strong> {info.error}
        </div>
      )}

      <ContextActionRenderer
        place={{ page: "plugin", pluginId: info.plugin_id }}
        heading="Actions"
        style={{ marginBottom: "var(--space-lg)" }}
      />
    </>
  );
}
