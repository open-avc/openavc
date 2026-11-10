/**
 * The Theme Studio's Elements section: one collapsible section per element
 * type (`elementControls.ts`), each control writing a theme variable or the
 * type's element default, marked and resettable while it differs from the
 * saved theme. The section also decides which type's section is open: a
 * control clicked in the preview (the panel posts its type, same origin only)
 * opens that section alone, scrolls to it and outlines it for a moment, and the
 * arrow keys on a section's name move to the next or previous section and open
 * it alone. It is drawn only once a theme has loaded, so a click before then
 * opens nothing.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ColorPickerCell } from "./ColorPickerCell";
import { CSS_VAR_FALLBACKS } from "./colorUtils";
import {
  ELEMENT_CONTROLS, ELEMENT_ORDER, ELEMENT_TYPE_LABELS, hasElementSection, type ElementControl,
} from "./elementControls";
import { ResetToSaved, StudioSection } from "./StudioParts";
import { differsFromSaved } from "./themeEdits";
import { displayStyleValue, storeStyleValue } from "./uiBuilderHelpers";

type Refs = Record<string, HTMLDetailsElement | null>;
type ElementDefaults = Record<string, Record<string, unknown>>;

interface ElementSectionsProps {
  vars: Record<string, unknown>;
  defaults: ElementDefaults;
  savedVars: Record<string, unknown>;
  savedDefaults: ElementDefaults;
  onSetVar: (key: string, value: unknown) => void;
  onSetElementDefault: (elType: string, key: string, value: unknown) => void;
}

export function ElementSections({ vars, defaults, savedVars, savedDefaults, onSetVar, onSetElementDefault }: ElementSectionsProps) {
  const refs = useRef<Refs>({});
  const bodyRef = useRef<HTMLDivElement>(null);
  const focused = usePreviewClicks();
  useOpenWhenClicked(refs, focused.type, focused.clear);
  useArrowKeys(bodyRef, refs);

  const valueOf = (type: string, control: ElementControl, from: { vars: Record<string, unknown>; defaults: ElementDefaults }) =>
    control.source.kind === "var" ? from.vars[control.source.key] : from.defaults[type]?.[control.source.key];
  const setter = (type: string, control: ElementControl) => (next: unknown) => {
    if (control.source.kind === "var") onSetVar(control.source.key, next);
    else onSetElementDefault(type, control.source.key, next);
  };

  return (
    <StudioSection title="Elements">
      <div ref={bodyRef} style={{ padding: 10 }}>
        {ELEMENT_ORDER.filter((type) => ELEMENT_CONTROLS[type]).map((type) => (
          <details key={type} ref={(el) => { refs.current[type] = el; }} style={{ marginBottom: 6 }}>
            <summary
              style={{ cursor: "pointer", fontSize: 12, fontWeight: 600, color: "var(--text-primary)", padding: "4px 0" }}
            >
              {ELEMENT_TYPE_LABELS[type] || type}
            </summary>
            <div style={{ paddingLeft: 12, paddingTop: 4, display: "flex", flexDirection: "column", gap: 4 }}>
              {ELEMENT_CONTROLS[type].map((control) => (
                <ControlRow
                  key={`${control.source.kind}:${control.source.key}`}
                  control={control}
                  value={valueOf(type, control, { vars, defaults })}
                  saved={valueOf(type, control, { vars: savedVars, defaults: savedDefaults })}
                  onChange={setter(type, control)}
                />
              ))}
            </div>
          </details>
        ))}
      </div>
    </StudioSection>
  );
}

/** The element type a control clicked in the preview asks for, until it has been opened. */
function usePreviewClicks() {
  const [type, setType] = useState<string | null>(null);
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      // The preview is served from this origin; another window must not pick the section.
      if (e.origin !== window.location.origin) return;
      if (e.data?.type !== "openavc:theme-element-click") return;
      const elType = e.data.elementType as string;
      if (hasElementSection(elType)) setType(elType);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
  const clear = useCallback(() => setType(null), []);
  return { type, clear };
}

const OUTLINE_MS = 1200;

function setOutline(el: HTMLElement, on: boolean) {
  el.style.outline = on ? "2px solid var(--accent)" : "";
  el.style.outlineOffset = on ? "-2px" : "";
  el.style.borderRadius = on ? "4px" : "";
}

/** Open the asked-for section alone, scroll to it and outline it for a moment, then forget the ask. */
function useOpenWhenClicked(refs: { current: Refs }, type: string | null, clear: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    if (!type) return;
    if (timer.current) clearTimeout(timer.current);
    for (const ref of Object.values(refs.current)) if (ref) setOutline(ref, false);
    for (const [t, ref] of Object.entries(refs.current)) if (ref) ref.open = t === type;
    const target = refs.current[type];
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      setOutline(target, true);
      timer.current = setTimeout(() => {
        setOutline(target, false);
        timer.current = undefined;
      }, OUTLINE_MS);
    }
    clear();
  }, [refs, type, clear]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
}

/** ArrowUp and ArrowDown on a section's name: the previous or next section, opened alone, its name focused. */
function useArrowKeys(bodyRef: { current: HTMLDivElement | null }, refs: { current: Refs }) {
  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      const current = focusedSectionIndex(body, refs.current);
      if (current < 0) return;
      e.preventDefault();
      const next = refs.current[ELEMENT_ORDER[stepped(current, e.key === "ArrowDown")]];
      if (next) openFromKeys(refs.current, next);
    };
    body.addEventListener("keydown", onKey);
    return () => body.removeEventListener("keydown", onKey);
  }, [bodyRef, refs]);
}

/** The next place in the order, or the previous, stopping at either end. */
function stepped(current: number, down: boolean): number {
  return down ? Math.min(current + 1, ELEMENT_ORDER.length - 1) : Math.max(current - 1, 0);
}

function openFromKeys(refs: Refs, next: HTMLDetailsElement) {
  for (const ref of Object.values(refs)) if (ref) ref.open = false;
  next.open = true;
  next.querySelector("summary")?.focus();
  next.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/** Which section's name has the focus, as its place in the order; -1 when none. */
function focusedSectionIndex(body: HTMLElement, refs: Refs): number {
  const active = document.activeElement;
  if (!active || !body.contains(active)) return -1;
  const details = active.closest("summary")?.parentElement;
  if (!details) return -1;
  return ELEMENT_ORDER.indexOf(Object.entries(refs).find(([, ref]) => ref === details)?.[0] || "");
}

interface ControlRowProps {
  control: ElementControl;
  value: unknown;
  saved: unknown;
  onChange: (next: unknown) => void;
}

const isBoxShadow = (control: ElementControl) =>
  control.source.kind === "default" && control.source.key === "box_shadow";

function ControlRow({ control, value, saved, onChange }: ControlRowProps) {
  const modified = differsFromSaved(value, saved);
  const tall = isBoxShadow(control);
  return (
    <div>
      <div style={{ display: "flex", gap: 6, alignItems: tall ? "flex-start" : "center" }}>
        <label
          title={control.hint}
          style={{
            width: 130,
            fontSize: 10,
            color: modified ? "var(--accent)" : "var(--text-muted)",
            fontWeight: modified ? 600 : 400,
            paddingTop: tall ? 4 : 0,
          }}
        >
          {control.label}
        </label>
        {controlField(control, value, onChange)}
        {modified && <ResetToSaved saved={saved} size={10} onReset={() => onChange(saved)} />}
      </div>
      {control.hint && (
        <div style={{ fontSize: 9, color: "var(--text-muted)", paddingLeft: 136, marginTop: 1, lineHeight: 1.3 }}>
          {control.hint}
        </div>
      )}
    </div>
  );
}

/** The field a control is edited in, by its type. */
function controlField(control: ElementControl, value: unknown, onChange: (next: unknown) => void) {
  if (control.type === "color") return colorField(control, value, onChange);
  if (control.type === "number") return lengthField(control.source.key, value, onChange);
  const text = String(value ?? "");
  if (isBoxShadow(control)) {
    return (
      <textarea
        value={text}
        onChange={(e) => onChange(e.target.value)}
        placeholder="none"
        title={text}
        rows={2}
        spellCheck={false}
        style={{ flex: 1, fontSize: 10, fontFamily: "monospace", resize: "vertical", minHeight: 32, lineHeight: 1.3 }}
      />
    );
  }
  return (
    <input
      type="text"
      value={text}
      onChange={(e) => onChange(e.target.value)}
      placeholder="none"
      title={text}
      style={{ flex: 1, fontSize: 10, fontFamily: "monospace" }}
    />
  );
}

/**
 * A colour: the swatch and the text box. A cleared colour that lives in a
 * variable inherits the panel's own default for it, which is shown; one that
 * lives in the element defaults inherits from the theme, with no one value to
 * show, so it only says so.
 */
function colorField(control: ElementControl, value: unknown, onChange: (next: unknown) => void) {
  const fallback = control.source.kind === "var" ? CSS_VAR_FALLBACKS[control.source.key] : undefined;
  const inherited = value == null || value === "";
  const text = String(value ?? "");
  return (
    <>
      <ColorPickerCell value={text} fallback={fallback} onChange={(next) => onChange(next)} />
      <input
        type="text"
        value={text}
        placeholder={inherited ? (fallback ? `${fallback} (inherited)` : "inherited") : ""}
        title={inherited ? (fallback ? `Inherits ${fallback}` : "Inherits the theme default") : undefined}
        onChange={(e) => onChange(e.target.value)}
        style={{ flex: 1, fontSize: 10, fontFamily: "monospace" }}
      />
    </>
  );
}

/** A length typed in pixels and stored in rem; emptied, it is cleared. */
function lengthField(key: string, value: unknown, onChange: (next: unknown) => void) {
  return (
    <input
      type="number"
      value={value == null || value === "" ? "" : (displayStyleValue(key, Number(value)) as number)}
      onChange={(e) => onChange(e.target.value === "" ? undefined : storeStyleValue(key, Number(e.target.value)))}
      style={{ flex: 1, fontSize: 10 }}
    />
  );
}
