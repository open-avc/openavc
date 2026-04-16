import { useState } from "react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { VariablesActions, VariablesSubTab } from "./variables/VariableEditor";
import { DeviceStatesSubTab } from "./variables/DeviceStateViewer";
import { ActivitySubTab } from "./variables/ActivityView";
import { subTabBarStyle, subTabBtnStyle } from "./variables/variablesShared";

type SubTab = "variables" | "device_states" | "activity";

export function VariablesView() {
  const [subTab, setSubTab] = useState<SubTab>("variables");

  return (
    <ViewContainer
      title="State"
      actions={subTab === "variables" ? <VariablesActions /> : undefined}
    >
      {/* Sub-tab bar */}
      <div style={subTabBarStyle} role="tablist">
        {([
          { key: "variables" as const, label: "Variables" },
          { key: "device_states" as const, label: "Device States" },
          { key: "activity" as const, label: "Activity" },
        ]).map((tab) => (
          <button
            key={tab.key}
            role="tab"
            aria-selected={subTab === tab.key}
            aria-controls={`tabpanel-${tab.key}`}
            onClick={() => setSubTab(tab.key)}
            style={{
              ...subTabBtnStyle,
              borderBottom: subTab === tab.key ? "2px solid var(--accent)" : "2px solid transparent",
              color: subTab === tab.key ? "var(--accent)" : "var(--text-secondary)",
              fontWeight: subTab === tab.key ? 600 : 400,
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`tabpanel-${subTab}`} style={{ flex: 1, overflow: "hidden" }}>
        {subTab === "variables" && <VariablesSubTab />}
        {subTab === "device_states" && <DeviceStatesSubTab />}
        {subTab === "activity" && <ActivitySubTab />}
      </div>
    </ViewContainer>
  );
}
