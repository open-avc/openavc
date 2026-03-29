import type { UIElement } from "../../../api/types";
import { ButtonRenderer } from "./ButtonRenderer";
import { LabelRenderer } from "./LabelRenderer";
import { SliderRenderer } from "./SliderRenderer";
import { StatusLedRenderer } from "./StatusLedRenderer";
import { PageNavRenderer } from "./PageNavRenderer";
import { SelectRenderer } from "./SelectRenderer";
import { TextInputRenderer } from "./TextInputRenderer";
import { ImageRenderer } from "./ImageRenderer";
import { SpacerRenderer } from "./SpacerRenderer";
import { CameraPresetRenderer } from "./CameraPresetRenderer";
import { GaugeRenderer } from "./GaugeRenderer";
import { LevelMeterRenderer } from "./LevelMeterRenderer";
import { FaderRenderer } from "./FaderRenderer";
import { GroupRenderer } from "./GroupRenderer";
import { ClockRenderer } from "./ClockRenderer";
import { KeypadRenderer } from "./KeypadRenderer";
import { MatrixRenderer } from "./MatrixRenderer";
import { ListRenderer } from "./ListRenderer";
import { GenericRenderer } from "./GenericRenderer";

interface RenderElementProps {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
  themeDefaults?: Record<string, Record<string, unknown>>;
}

export function RenderElement({
  element,
  previewMode,
  liveState,
  themeDefaults,
}: RenderElementProps) {
  // Merge theme defaults for this element type into the element's style
  // Theme defaults act as base values; explicit element styles override
  const themedElement = themeDefaults?.[element.type]
    ? {
        ...element,
        style: { ...themeDefaults[element.type], ...element.style },
      }
    : element;

  switch (themedElement.type) {
    case "button":
      return (
        <ButtonRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "label":
      return (
        <LabelRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "slider":
      return (
        <SliderRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "status_led":
      return (
        <StatusLedRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "page_nav":
      return (
        <PageNavRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "select":
      return (
        <SelectRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "text_input":
      return (
        <TextInputRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "image":
      return (
        <ImageRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "spacer":
      return (
        <SpacerRenderer element={themedElement} previewMode={previewMode} />
      );
    case "camera_preset":
      return (
        <CameraPresetRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "gauge":
      return (
        <GaugeRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "level_meter":
      return (
        <LevelMeterRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "fader":
      return (
        <FaderRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "group":
      return (
        <GroupRenderer element={themedElement} previewMode={previewMode} />
      );
    case "clock":
      return (
        <ClockRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "keypad":
      return (
        <KeypadRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "matrix":
      return (
        <MatrixRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "list":
      return (
        <ListRenderer
          element={themedElement}
          previewMode={previewMode}
          liveState={liveState}
        />
      );
    case "plugin":
      return (
        <PluginElementRenderer
          element={themedElement}
          previewMode={previewMode}
        />
      );
    default:
      return <GenericRenderer element={themedElement} />;
  }
}

function PluginElementRenderer({
  element,
  previewMode,
}: {
  element: UIElement;
  previewMode: boolean;
}) {
  const pluginId = element.plugin_id || "unknown";
  const pluginType = element.plugin_type || "unknown";

  if (previewMode && pluginId && pluginType) {
    // In preview mode, render actual iframe
    const src = `/api/plugins/${pluginId}/panel/${pluginType}.html`;
    return (
      <div
        style={{
          width: "100%",
          height: "100%",
          overflow: "hidden",
          borderRadius: "inherit",
        }}
      >
        <iframe
          src={src}
          sandbox="allow-scripts allow-same-origin"
          style={{
            width: "100%",
            height: "100%",
            border: "none",
            borderRadius: "inherit",
          }}
          title={`Plugin: ${pluginType}`}
        />
      </div>
    );
  }

  // In design mode, show placeholder
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 4,
        background: "rgba(156, 39, 176, 0.08)",
        border: "1px dashed rgba(156, 39, 176, 0.4)",
        borderRadius: "inherit",
        color: "var(--text-secondary)",
        fontSize: 12,
      }}
    >
      <div style={{ fontSize: 20, opacity: 0.5 }}>&#x1F9E9;</div>
      <div style={{ fontWeight: 600 }}>{element.label || pluginType}</div>
      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
        Plugin: {pluginId}
      </div>
    </div>
  );
}
