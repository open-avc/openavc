/**
 * The Basic section of the UI Builder's Properties panel: what every element
 * has (its ID, type, tag and, where it is drawn, its label), then the fields its
 * type has, from one table, then the grant and icon rows the types that have
 * them share.
 *
 * The bigger types' fields live in their own modules (MatrixFields,
 * ValueFields, ClockFields, ButtonImageFields, PluginElementConfig); the rows
 * they are built from are FieldRow's and StyleRows'.
 */
import { useState, useEffect } from "react";
import { Plus, X } from "lucide-react";
import type { UIElement, UIPage, UIElementOption, Placement, ProjectConfig } from "../../../api/types";
import { CopyButton } from "../../shared/CopyButton";
import { IconPicker } from "../IconPicker";
import { AssetPicker } from "../AssetPicker";
import { getAssetUrl } from "../../../api/systemClient";
import { pxToRem, remToPx } from "../uiBuilderHelpers";
import { KEYPAD_DIGITS_DEFAULT, KEYPAD_SEND_DELAY_DEFAULT_MS } from "../reviewProperties";
import { HONORED_PROPERTIES } from "../../../api/uiBindingReach.gen";
import { InlineColorPicker } from "../../shared/InlineColorPicker";
import { SearchableSelect } from "../../shared/SearchableSelect";
import { pageGroups } from "../../shared/pickerOptions";
import { CustomControlConfig } from "./CustomControlConfig";
import { CheckboxRow, FieldHint, FieldRow, type FieldProps } from "./FieldRow";
import { ButtonImageFields } from "./ButtonImageFields";
import { ClockFields } from "./ClockFields";
import { MatrixFields } from "./MatrixFields";
import { PluginElementConfig } from "./PluginElementConfig";
import { FaderFields, GaugeFields, LevelMeterFields, ShownDecimalsRow, SliderFields } from "./ValueFields";
import { useRowKeys } from "./rowKeys";
import { GrantEditor } from "./GrantEditor";
import { numOrUndefined } from "./numericField";

/**
 * Whether this element type's renderer draws `label`, per the panel's own
 * table. An unknown type (a plugin element the table has never seen) keeps the
 * field: silence there means "not recorded", not "not drawn".
 */
function drawsLabel(type: string): boolean {
  const honored = Object.prototype.hasOwnProperty.call(HONORED_PROPERTIES, type)
    ? HONORED_PROPERTIES[type]
    : null;
  return honored ? honored.includes("label") : type !== "label";
}

interface BasicPropertiesProps {
  element: UIElement;
  /** Read by the matrix setup picker, which needs the device list. */
  project: ProjectConfig;
  pages: UIPage[];
  macros?: { id: string; name: string }[];
  /** Where the element sits: geometry lives in the page's layout, so turning
   *  a slider on its side is handed both halves to turn its box too. */
  placement?: Placement;
  onChangePlacement?: (placement: Placement) => void;
  onChange: (patch: Partial<UIElement>) => void;
  onRename?: (newId: string) => void;
}

export function BasicProperties({
  element,
  project,
  pages,
  macros = [],
  placement,
  onChangePlacement,
  onChange,
  onRename,
}: BasicPropertiesProps) {
  useIntrinsicAspect(element, onChange);
  const TypeFields = typeFieldsFor(element.type);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {/* ID — inline rename, with reference rewriting in onRename handler */}
      <FieldRow label="ID">
        <ElementIdField id={element.id} onRename={onRename} />
        <CopyButton value={element.id} title="Copy element ID" />
      </FieldRow>

      {/* Type (read-only) */}
      <FieldRow label="Type">
        <input
          value={element.type}
          readOnly
          style={{
            flex: 1,
            opacity: 0.6,
            cursor: "default",
            background: "var(--bg-surface)",
          }}
        />
      </FieldRow>

      {/* Tag -- what this control calls itself when it runs a macro. Free
          text on purpose: "1", "hdmi2" and "row_3" are all the same to the
          platform, and the author is naming their own array. */}
      <FieldRow label="Tag">
        <input
          value={element.tag || ""}
          onChange={(e) => onChange({ tag: e.target.value || undefined })}
          placeholder="optional"
          style={{ flex: 1 }}
        />
      </FieldRow>
      <div
        style={{
          fontSize: "var(--font-size-xs, 11px)",
          color: "var(--text-muted)",
          margin: "-4px 0 4px 0",
        }}
      >
        A macro this control runs reads it as <code>$trigger.tag</code>, so a row
        of buttons can share one macro instead of needing one each.
      </div>

      {drawsLabel(element.type) && <LabelFields element={element} onChange={onChange} />}
      {TypeFields && (
        <TypeFields
          element={element}
          project={project}
          pages={pages}
          macros={macros}
          placement={placement}
          onChangePlacement={onChangePlacement}
          onChange={onChange}
        />
      )}
      {GRANTED_TYPES.includes(element.type) && (
        <GrantEditor grant={element.grant} onChange={(grant) => onChange({ grant })} />
      )}
      {ICON_TYPES.includes(element.type) && <IconFields element={element} onChange={onChange} />}
    </div>
  );
}

/** What a type's fields may read beyond the element itself. */
interface SectionProps extends FieldProps {
  project: ProjectConfig;
  pages: UIPage[];
  macros: { id: string; name: string }[];
  placement?: Placement;
  onChangePlacement?: (placement: Placement) => void;
}

/**
 * The fields each element type has beyond the ones every element has, in the
 * order they are drawn. A type with no row here (a plugin type the Builder has
 * never seen) gets only those.
 */
const TYPE_FIELDS: Record<string, (props: SectionProps) => React.JSX.Element> = {
  button: ButtonFields,
  label: LabelTextFields,
  page_nav: PageNavFields,
  slider: SliderFields,
  text_input: TextInputFields,
  image: ImageFields,
  camera_preset: CameraPresetFields,
  gauge: GaugeFields,
  level_meter: LevelMeterFields,
  fader: FaderFields,
  group: GroupFields,
  clock: ClockFields,
  list: ListFields,
  matrix: MatrixFields,
  keypad: KeypadFields,
  custom: CustomFields,
  plugin: PluginFields,
  select: SelectFields,
};

/**
 * The two element types that run someone else's page, and so take a grant of
 * what it may touch. Its rows come after the type's own, in one place for both,
 * so selecting one after the other keeps them.
 */
const GRANTED_TYPES = ["custom", "plugin"];

/** The types that draw an icon beside their words; their icon rows close the section, shared the same way. */
const ICON_TYPES = ["button", "label", "page_nav", "camera_preset"];

/** The row for this type, read as the table's own: `constructor` is not a type. */
function typeFieldsFor(type: string): ((props: SectionProps) => React.JSX.Element) | null {
  return Object.prototype.hasOwnProperty.call(TYPE_FIELDS, type) ? TYPE_FIELDS[type] : null;
}


/** Whether the element's text keeps the line breaks typed into it. */
function isMultiLine(element: UIElement): boolean {
  return element.style?.white_space === "pre-line" || element.style?.white_space === "pre-wrap";
}

/** A text the element draws: one line, or several once its line breaks are kept. */
function MultiLineTextField({ element, onChange, field }: FieldProps & { field: "label" | "text" }) {
  return isMultiLine(element) ? (
    <textarea
      value={element[field] || ""}
      onChange={(e) => onChange({ [field]: e.target.value } as Partial<UIElement>)}
      rows={3}
      style={{ flex: 1, resize: "vertical", fontSize: "var(--font-size-sm)" }}
    />
  ) : (
    <input
      value={element[field] || ""}
      onChange={(e) => onChange({ [field]: e.target.value } as Partial<UIElement>)}
      style={{ flex: 1 }}
    />
  );
}

/** Keep the line breaks typed into the text; off removes the setting, so the theme's applies. */
function MultiLineToggle({ element, onChange }: FieldProps) {
  return (
    <CheckboxRow
      label="Multi-line"
      checked={isMultiLine(element)}
      onToggle={(on) => {
        const updated = { ...(element.style || {}) };
        if (on) {
          updated.white_space = "pre-line";
        } else {
          delete updated.white_space;
        }
        onChange({ style: updated });
      }}
    >
      Use Enter for line breaks
    </CheckboxRow>
  );
}

/** The types whose label is drawn on a button face, where a line break can go. */
const LABEL_LINE_BREAK_TYPES = ["button", "page_nav", "camera_preset"];

/**
 * The label, for the types whose renderer draws one. Only those: a field on a
 * type that never draws it is how a project ends up carrying a caption nobody
 * can see, which the page review then warns about.
 */
function LabelFields({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Label">
        <MultiLineTextField element={element} onChange={onChange} field="label" />
      </FieldRow>
      {LABEL_LINE_BREAK_TYPES.includes(element.type) && (
        <MultiLineToggle element={element} onChange={onChange} />
      )}
    </>
  );
}

/** A button: what it shows, and its picture. */
function ButtonFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Display">
        <select
          value={element.display_mode || "text"}
          onChange={(e) => onChange({ display_mode: e.target.value === "text" ? undefined : e.target.value })}
          style={{ flex: 1 }}
        >
          <option value="text">Text Only</option>
          <option value="icon_text">Icon + Text</option>
          <option value="icon_only">Icon Only</option>
          <option value="image">Image</option>
          <option value="image_text">Image + Text</option>
        </select>
      </FieldRow>
      {(element.display_mode === "image" || element.display_mode === "image_text") && (
        <ButtonImageFields element={element} onChange={onChange} />
      )}
    </>
  );
}

/** A label's own text, its line breaks and its rounding. */
function LabelTextFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Text">
        <MultiLineTextField element={element} onChange={onChange} field="text" />
      </FieldRow>
      <MultiLineToggle element={element} onChange={onChange} />
      <ShownDecimalsRow element={element} onChange={onChange} placeholder="As reported" />
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: -2, marginBottom: 4 }}>
        Rounds a bound value when it is a number. Text is shown exactly as the device reports it.
      </div>
    </>
  );
}

/** A page nav: where it goes, and whether it lights up there. */
function PageNavFields({ element, pages, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Target Page">
        <SearchableSelect
          value={element.target_page || ""}
          onChange={(target_page) => onChange({ target_page })}
          groups={[
            { options: [{ value: "$back", label: "$back (dismiss overlay)", hint: "$back" }] },
            ...pageGroups(pages),
          ]}
          placeholder="Select page..."
          searchPlaceholder="Search pages..."
          style={{ flex: 1 }}
        />
      </FieldRow>
      <CheckboxRow
        label="Current page"
        checked={!!element.highlight_current}
        onToggle={(on) => onChange({ highlight_current: on || undefined })}
      >
        Light up while its page is showing
      </CheckboxRow>
    </>
  );
}

/** Where an image's picture comes from when it is not one of the project's own. */
function externalUrl(src: string | undefined): string {
  return src?.startsWith("assets://") ? "" : (src || "");
}

/** An image's picture, its address, its description and how it fits. */
function ImageFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Image">
        <AssetPicker
          value={element.src || ""}
          onChange={(v) => onChange({ src: v || undefined })}
        />
      </FieldRow>
      <FieldRow label="URL">
        <input
          value={externalUrl(element.src)}
          onChange={(e) => onChange({ src: e.target.value || undefined })}
          placeholder="Or enter external URL..."
          style={{ flex: 1, fontSize: 11 }}
        />
      </FieldRow>
      <FieldRow label="Alt Text">
        <input
          value={element.label || ""}
          onChange={(e) => onChange({ label: e.target.value || undefined })}
          placeholder="Describe the image"
          style={{ flex: 1, fontSize: 11 }}
        />
      </FieldRow>
      <FieldRow label="Fit">
        <select
          value={element.object_fit || "contain"}
          onChange={(e) => onChange({ object_fit: e.target.value })}
          style={{ flex: 1 }}
        >
          <option value="contain">Contain (fit, may letterbox)</option>
          <option value="cover">Cover (fill, may crop)</option>
          <option value="fill">Fill (stretch to fit)</option>
        </select>
      </FieldRow>
    </>
  );
}

/** A list's style, row height and items. */
function ListFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Style">
        <select value={element.list_style || "selectable"} onChange={(e) => onChange({ list_style: e.target.value })} style={{ flex: 1 }}>
          <option value="static">Static (read-only)</option>
          <option value="selectable">Selectable</option>
          <option value="multi_select">Multi-Select</option>
          <option value="action">Action</option>
        </select>
      </FieldRow>
      <FieldRow label="Item Height">
        <input type="number" value={remToPx(element.item_height) ?? ""} onChange={(e) => onChange({ item_height: pxToRem(numOrUndefined(e.target.value)) ?? undefined })} placeholder="44" min={24} max={120} style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </FieldRow>
      <LabelValueRows
        rows={element.items ?? []}
        onChange={(items) => onChange({ items })}
        title="Items"
        noun="Item"
        emptyText="No items. Click Add to create one, or use an Items binding for dynamic content."
      />
      <div style={{ fontSize: 10, color: "var(--text-muted)", fontStyle: "italic" }}>
        For dynamic items, use the Items binding to populate from state keys.
      </div>
    </>
  );
}

/** A keypad's digits, layout, sending and display. */
function KeypadFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Digits">
        <input type="number" value={element.digits ?? ""} onChange={(e) => onChange({ digits: numOrUndefined(e.target.value) })} placeholder={String(KEYPAD_DIGITS_DEFAULT)} min={1} max={10} style={{ flex: 1 }} />
      </FieldRow>
      <FieldRow label="Style">
        <select value={element.keypad_style || "numeric"} onChange={(e) => onChange({ keypad_style: e.target.value })} style={{ flex: 1 }}>
          <option value="numeric">Numeric (0-9)</option>
          <option value="phone">Phone (* #)</option>
        </select>
      </FieldRow>
      <CheckboxRow label="Auto Send" checked={element.auto_send ?? false} onToggle={(on) => onChange({ auto_send: on })}>
        Send without Enter
      </CheckboxRow>
      <FieldHint>
        {(element.keypad_style || "numeric") === "phone"
          ? "The phone layout has no Enter key, so it sends only this way: as soon as Digits keys are pressed, or after the Send Delay with no key press."
          : "Sends as soon as Digits keys are pressed, or after the Send Delay with no key press. With this off, Enter sends."}
      </FieldHint>
      {(element.auto_send ?? false) && <SendDelayField element={element} onChange={onChange} />}
      <CheckboxRow
        label="Show Display"
        checked={element.show_display ?? true}
        onToggle={(on) => onChange({ show_display: on })}
      >
        Show digit display above keys
      </CheckboxRow>
      {(element.show_display ?? true) && (
        <CheckboxRow
          label="Mask Display"
          checked={element.mask_display ?? false}
          onToggle={(on) => onChange({ mask_display: on })}
        >
          Show a dot for each digit typed
        </CheckboxRow>
      )}
    </>
  );
}

/** How long a keypad sending on its own waits after the last key press. */
function SendDelayField({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Send Delay">
        <input
          type="number"
          value={element.auto_send_delay_ms ?? KEYPAD_SEND_DELAY_DEFAULT_MS}
          onChange={(e) => onChange({ auto_send_delay_ms: e.target.value ? Number(e.target.value) : undefined })}
          min={0} max={5000} step={100} style={{ flex: 1 }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>ms</span>
      </FieldRow>
      <FieldHint>
        How long after the last key press a shorter entry is sent. Under {KEYPAD_SEND_DELAY_DEFAULT_MS} ms, a code can be sent before it is finished.
      </FieldHint>
    </>
  );
}

/** A select's options. */
function SelectFields({ element, onChange }: SectionProps) {
  return (
    <LabelValueRows
      rows={element.options ?? []}
      onChange={(options) => onChange({ options })}
      title="Options"
      noun="Option"
      emptyText="No options. Click Add to create one."
    />
  );
}

/**
 * Rows of a label and a value, added to and removed from: a select's options,
 * a list's items. A new row is named `<noun> N`, its value `<noun>_N`.
 */
function LabelValueRows({
  rows,
  onChange,
  title,
  noun,
  emptyText,
}: {
  rows: UIElementOption[];
  onChange: (rows: UIElementOption[]) => void;
  title: string;
  noun: string;
  emptyText: string;
}) {
  const rowKeys = useRowKeys(rows.length);
  const addRow = () => {
    onChange([...rows, { label: `${noun} ${rows.length + 1}`, value: `${noun.toLowerCase()}_${rows.length + 1}` }]);
  };
  const removeRow = (index: number) => {
    rowKeys.removeAt(index);
    onChange(rows.filter((_, i) => i !== index));
  };
  const updateRow = (index: number, patch: Partial<UIElementOption>) => {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-xs)" }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>{title}</label>
        <button
          onClick={addRow}
          style={{ display: "flex", alignItems: "center", gap: 2, padding: "2px 6px", borderRadius: "var(--border-radius)", fontSize: 11, color: "var(--accent)" }}
        >
          <Plus size={12} /> Add
        </button>
      </div>
      {rows.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "var(--space-xs)" }}>
          {emptyText}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {rows.map((row, i) => (
          <div key={rowKeys.keys[i]} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input
              value={row.label}
              onChange={(e) => updateRow(i, { label: e.target.value })}
              placeholder="Label"
              style={{ flex: 1, padding: "3px 6px", fontSize: 11 }}
            />
            <input
              value={row.value}
              onChange={(e) => updateRow(i, { value: e.target.value })}
              placeholder="Value"
              style={{ flex: 1, padding: "3px 6px", fontSize: 11 }}
            />
            <button onClick={() => removeRow(i)} style={{ display: "flex", padding: 2, color: "var(--text-muted)" }}>
              <X size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** A text input's placeholder. */
function TextInputFields({ element, onChange }: SectionProps) {
  return (
    <FieldRow label="Placeholder">
      <input
        value={element.placeholder || ""}
        onChange={(e) => onChange({ placeholder: e.target.value })}
        placeholder="Placeholder text..."
        style={{ flex: 1 }}
      />
    </FieldRow>
  );
}

/** A camera preset's number. */
function CameraPresetFields({ element, onChange }: SectionProps) {
  return (
    <FieldRow label="Preset #">
      <input
        type="number"
        value={element.preset_number ?? ""}
        onChange={(e) =>
          onChange({
            preset_number: e.target.value
              ? Number(e.target.value)
              : undefined,
          })
        }
        min={1}
        style={{ flex: 1 }}
      />
    </FieldRow>
  );
}

/** Where a group draws its label. */
function GroupFields({ element, onChange }: SectionProps) {
  return (
    <>
      <FieldRow label="Label Pos">
        <select value={element.label_position || "top-left"} onChange={(e) => onChange({ label_position: e.target.value })} style={{ flex: 1 }}>
          <option value="top-left">Top Left</option>
          <option value="top-center">Top Center</option>
          <option value="top-right">Top Right</option>
          <option value="bottom-left">Bottom Left</option>
          <option value="bottom-center">Bottom Center</option>
          <option value="bottom-right">Bottom Right</option>
        </select>
      </FieldRow>
    </>
  );
}

/** A custom control: the page in the project's ui/ folder it runs. */
function CustomFields({ element, onChange }: SectionProps) {
  return (
    <CustomControlConfig
      file={element.custom_file || ""}
      config={element.custom_config || {}}
      onChange={onChange}
    />
  );
}

/** A plugin element: its settings. */
function PluginFields({ element, onChange }: SectionProps) {
  return (
    <PluginElementConfig
      pluginId={element.plugin_id || ""}
      pluginType={element.plugin_type || ""}
      config={element.plugin_config || {}}
      onChange={(cfg) => onChange({ plugin_config: cfg })}
    />
  );
}

/** The icon a text-drawing control shows beside its words. */
function IconFields({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Icon">
        <IconPicker
          value={element.icon || ""}
          onChange={(v) => onChange({ icon: v || undefined })}
        />
      </FieldRow>
      {element.icon && (
        <>
          <FieldRow label="Position">
            <select
              value={element.icon_position || "left"}
              onChange={(e) => onChange({ icon_position: e.target.value })}
              style={{ flex: 1 }}
            >
              <option value="left">Left</option>
              <option value="right">Right</option>
              <option value="top">Top</option>
              <option value="bottom">Bottom</option>
              <option value="center">Icon Only</option>
            </select>
          </FieldRow>
          <FieldRow label="Icon Size">
            <input
              type="number"
              value={remToPx(element.icon_size) ?? 24}
              onChange={(e) =>
                onChange({
                  icon_size: e.target.value ? pxToRem(Number(e.target.value)) ?? undefined : undefined,
                })
              }
              min={12}
              max={64}
              style={{ width: 64 }}
            />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
          </FieldRow>
          <FieldRow label="Icon Color">
            <InlineColorPicker size="md" clearable
              value={element.icon_color || ""}
              onChange={(v) => onChange({ icon_color: v || undefined })}
            />
          </FieldRow>
        </>
      )}
    </>
  );
}

/**
 * Give a picture element the shape of the picture in it.
 *
 * This is the "intrinsic" aspect lock. It can't be written when the element is
 * created — a fresh image element has no picture and therefore no shape — so it
 * is written the moment a source is chosen, and only if the author has not
 * already set a lock of their own. The renderer invents nothing on its own, so
 * if the authoring surface doesn't do this, nothing does.
 *
 * It probes the committed source with this render's patch callback: a callback
 * retained by the picker would still hold the element from before selection.
 * Another edit, a new selection or an unmount makes that render stale, and
 * cancels the probe.
 */
function useIntrinsicAspect(element: UIElement, onChange: (patch: Partial<UIElement>) => void) {
  useEffect(() => {
    const src = pictureToMeasure(element);
    if (!src) return;
    const probe = new Image();
    let cancelled = false;
    probe.onload = () => {
      if (cancelled) return;
      const ratio = aspectOf(probe);
      if (ratio !== null) onChange({ aspect_lock: ratio });
    };
    probe.src = src.startsWith("assets://") ? getAssetUrl(src.slice("assets://".length)) : src;
    return () => { cancelled = true; };
  }, [element, onChange]);
}

/** The picture whose shape this element still needs, if it needs one. */
function pictureToMeasure(element: UIElement): string | null {
  if (element.type !== "image" || element.aspect_lock != null) return null;
  return element.src || null;
}

/** A loaded picture's width over its height, to four places; none for a zero side. */
function aspectOf(picture: HTMLImageElement): number | null {
  if (!picture.naturalWidth || !picture.naturalHeight) return null;
  return Math.round((picture.naturalWidth / picture.naturalHeight) * 10000) / 10000;
}

function ElementIdField({
  id,
  onRename,
}: {
  id: string;
  onRename?: (newId: string) => void;
}) {
  const [draft, setDraft] = useState(id);
  const [focused, setFocused] = useState(false);

  // Reset draft when the underlying id changes (selection switch, undo, etc.)
  // — only when the field isn't being actively edited.
  useEffect(() => {
    if (!focused) setDraft(id);
  }, [id, focused]);

  const commit = () => {
    if (!onRename) return;
    const trimmed = draft.trim();
    if (trimmed === id) return;
    onRename(trimmed);
    // The handler shows an error toast if invalid; reset the draft if the
    // parent didn't actually rename.
    setDraft(id);
  };

  return (
    <input
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={() => setFocused(true)}
      onBlur={() => {
        setFocused(false);
        commit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          (e.target as HTMLInputElement).blur();
        } else if (e.key === "Escape") {
          e.preventDefault();
          setDraft(id);
          (e.target as HTMLInputElement).blur();
        }
      }}
      readOnly={!onRename}
      title={onRename ? "Rename element (Enter to commit, Esc to cancel)" : "Element ID"}
      style={{
        flex: 1,
        background: onRename ? "var(--bg-primary)" : "var(--bg-surface)",
        cursor: onRename ? "text" : "default",
      }}
    />
  );
}
