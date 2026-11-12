import { UpdateRowButton } from "../shared/CatalogUpdateControls";
import type { CatalogUpdate } from "../shared/catalogUpdates";
import type { DriverInfo } from "../../api/types";
import { NOT_LOADED_TEXT } from "./installedDriverList";

/** One driver in the list: its name (a PY mark for a Python driver), its
 *  maker and category or that it did not load, and Update when there is one. */
export function DriverListRow({
  driver,
  selected,
  notLoaded,
  python,
  update,
  updating,
  onSelect,
  onUpdate,
}: {
  driver: DriverInfo;
  selected: boolean;
  notLoaded: boolean;
  python: boolean;
  update?: CatalogUpdate;
  updating: boolean;
  onSelect: () => void;
  onUpdate: (update: CatalogUpdate) => void;
}) {
  return (
    <div
      onClick={onSelect}
      style={{
        padding: "var(--space-sm) var(--space-md)",
        cursor: "pointer",
        borderBottom: "1px solid var(--border-color)",
        background:
          selected ? "var(--accent-dim, rgba(59,130,246,0.1))" : "transparent",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontWeight: 500,
          fontSize: "var(--font-size-sm)",
        }}
      >
        <span
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {driver.name}
        </span>
        {python && (
          <span
            title="Python driver"
            style={{
              fontSize: 10,
              padding: "1px 5px",
              borderRadius: 3,
              background: "var(--bg-hover)",
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              fontWeight: 600,
              flexShrink: 0,
            }}
          >
            PY
          </span>
        )}
      </div>
      <div
        style={{
          fontSize: "var(--font-size-xs)",
          color: notLoaded ? "var(--color-warning)" : "var(--text-muted)",
          marginTop: 2,
        }}
      >
        {notLoaded
          ? "Did not load"
          : <>{driver.manufacturer} &middot; {driver.category || "Other"}</>}
      </div>
      {update && (
        <div style={{ marginTop: 4 }}>
          <UpdateRowButton
            update={update}
            updating={updating}
            onUpdate={() => onUpdate(update)}
          />
        </div>
      )}
    </div>
  );
}

/** Said above a driver's page when its file never registered. */
export function NotLoadedNote({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <p
      style={{
        margin: "0 0 var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--radius)",
        border: "1px solid var(--color-warning)",
        fontSize: "var(--font-size-sm)",
        color: "var(--color-warning)",
      }}
    >
      {NOT_LOADED_TEXT}
    </p>
  );
}
