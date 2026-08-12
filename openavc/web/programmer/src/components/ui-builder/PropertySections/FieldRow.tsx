/**
 * One labelled row in the properties panel.
 *
 * Lived twice — identically — in `PropertiesPanel.tsx` and
 * `BasicProperties.tsx`, which is one copy too many for four lines of flexbox
 * and the reason a third would have appeared the moment a section moved into
 * its own file.
 */
export function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
      }}
    >
      <label
        style={{
          width: 72,
          flexShrink: 0,
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}
