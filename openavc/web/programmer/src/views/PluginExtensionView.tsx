/**
 * Renders a plugin-contributed view based on its extension definition.
 * Dispatches to PluginViewRenderer for the appropriate renderer type.
 */
import { Plug } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { PluginViewRenderer } from "../components/plugins/PluginExtensions";
import { usePluginStore } from "../store/pluginStore";

export function PluginExtensionView({ viewKey }: { viewKey: string }) {
  const views = usePluginStore((s) => s.extensions.views);

  // viewKey format: "plugin_id.view_id"
  const ext = views.find(
    (v) => `${v.plugin_id}.${v.id}` === viewKey
  );

  if (!ext) {
    return (
      <ViewContainer title="Plugin View">
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--text-muted)",
            gap: "var(--space-md)",
          }}
        >
          <Plug size={48} strokeWidth={1} />
          <div style={{ fontSize: "var(--font-size-sm)" }}>Plugin view not found</div>
        </div>
      </ViewContainer>
    );
  }

  return (
    <ViewContainer title={ext.label}>
      <PluginViewRenderer ext={ext} />
    </ViewContainer>
  );
}
