/**
 * The project stylesheet, read from the authoring side.
 *
 * `ui.custom_css` is one document the panel appends after the theme, and
 * `element.css_class` names which of its classes land on which control. The
 * pair only feels like one feature if the Builder can tell you what classes
 * the stylesheet actually defines -- otherwise naming a class is typing into
 * the dark and a typo shows up as "nothing happened" on the glass.
 *
 * So this reads the sheet well enough to list its class names. It is not a CSS
 * parser and does not need to be: nothing here decides what renders, it only
 * decides what to offer as a suggestion.
 */

/** Class names a valid CSS ident allows: never a leading digit, never `-<digit>`. */
const CLASS_IN_SELECTOR = /\.(-?[_a-zA-Z][-\w]*)/g;

/** A whole class name, anchored -- used to tell a typed name from a valid one. */
const VALID_CLASS_NAME = /^-?[_a-zA-Z][-\w]*$/;

/** Strip `/* ... *\/` comments. An unterminated one swallows the rest, like CSS does. */
function stripComments(css: string): string {
  let out = "";
  let i = 0;
  while (i < css.length) {
    const start = css.indexOf("/*", i);
    if (start === -1) return out + css.slice(i);
    out += css.slice(i, start);
    const end = css.indexOf("*/", start + 2);
    if (end === -1) return out;
    i = end + 2;
  }
  return out;
}

/**
 * Quoted strings and attribute selectors, blanked out.
 *
 * `content: ".foo"` and `[data-x=".bar"]` both hold a dot followed by a word
 * and neither is a class anyone can use. Blanking rather than deleting keeps
 * the rest of the text where it was.
 */
function blankStringsAndAttributes(prelude: string): string {
  let out = "";
  let quote: string | null = null;
  let inAttribute = false;
  for (const ch of prelude) {
    if (quote) {
      if (ch === quote) {
        quote = null;
        out += ch;
      } else {
        out += " ";
      }
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      out += ch;
      continue;
    }
    if (ch === "[") {
      inAttribute = true;
      out += ch;
      continue;
    }
    if (ch === "]") {
      inAttribute = false;
      out += ch;
      continue;
    }
    out += inAttribute ? " " : ch;
  }
  return out;
}

/** One style rule, reduced to what the Builder needs: who it hits, what it sets. */
interface StylesheetRule {
  classes: string[];
  properties: string[];
}

/**
 * The stylesheet's style rules, as (classes, properties) pairs.
 *
 * Only selectors are read for classes -- the text before a `{`. Declarations
 * inside a rule are never read as selectors, which is what keeps
 * `border-radius: 0.5rem` from being offered as a class called `5rem`. An
 * at-rule prelude (`@media (min-width: 37.5rem)`) is skipped for the same
 * reason; the rules nested inside it are read normally, so a class that only
 * exists in a media query still shows up.
 */
function stylesheetRules(css: string | null | undefined): StylesheetRule[] {
  if (!css || typeof css !== "string") return [];
  const text = stripComments(css);
  const rules: StylesheetRule[] = [];

  let buffer = "";
  // The prelude of the block we are inside, or null when that block is an
  // at-rule (whose body holds rules, not declarations).
  let openClasses: string[] | null = null;
  for (const ch of text) {
    if (ch === "{") {
      const isAtRule = buffer.trimStart().startsWith("@");
      if (isAtRule) {
        openClasses = null;
      } else {
        const prelude = blankStringsAndAttributes(buffer);
        openClasses = [...prelude.matchAll(CLASS_IN_SELECTOR)].map((m) => m[1]);
      }
      buffer = "";
      continue;
    }
    if (ch === "}") {
      if (openClasses) {
        rules.push({ classes: openClasses, properties: declaredProperties(buffer) });
        openClasses = null;
      }
      buffer = "";
      continue;
    }
    buffer += ch;
  }
  return rules;
}

/** Property names out of a declaration block: the text before each `:`. */
function declaredProperties(body: string): string[] {
  const names: string[] = [];
  for (const chunk of body.split(";")) {
    const at = chunk.indexOf(":");
    if (at === -1) continue;
    const name = chunk.slice(0, at).trim().toLowerCase();
    if (name) names.push(name);
  }
  return names;
}

/**
 * Every class name the stylesheet defines, in the order it first mentions them.
 */
export function stylesheetClassNames(css: string | null | undefined): string[] {
  const found: string[] = [];
  const seen = new Set<string>();
  for (const rule of stylesheetRules(css)) {
    for (const name of rule.classes) {
      if (seen.has(name)) continue;
      seen.add(name);
      found.push(name);
    }
  }
  return found;
}

/**
 * A CSS shorthand also sets the longhands underneath it.
 *
 * Only the handful that collide with what a control's feedback writes, because
 * the only consumer is the conflict warning below -- being exhaustive about
 * every shorthand in CSS would buy nothing and drift.
 */
const SHORTHAND_COVERS: Record<string, string[]> = {
  background: ["background-color", "background-image"],
  border: ["border-color", "border-width", "border-style"],
  "border-top": ["border-color", "border-width", "border-style"],
  "border-right": ["border-color", "border-width", "border-style"],
  "border-bottom": ["border-color", "border-width", "border-style"],
  "border-left": ["border-color", "border-width", "border-style"],
  font: ["font-size", "font-weight", "font-family"],
  all: [],
};

/** Which CSS properties each class in the sheet sets, shorthands expanded. */
export function stylesheetClassProperties(
  css: string | null | undefined,
): Map<string, Set<string>> {
  const byClass = new Map<string, Set<string>>();
  for (const rule of stylesheetRules(css)) {
    for (const name of rule.classes) {
      let set = byClass.get(name);
      if (!set) {
        set = new Set<string>();
        byClass.set(name, set);
      }
      for (const prop of rule.properties) {
        set.add(prop);
        for (const covered of SHORTHAND_COVERS[prop] ?? []) set.add(covered);
      }
    }
  }
  return byClass;
}

/** The classes on one element, as a list. `css_class` is space-separated, like the attribute it becomes. */
export function cssClassList(value: string | null | undefined): string[] {
  if (!value || typeof value !== "string") return [];
  return value.split(/\s+/).filter(Boolean);
}

/** Put the list back the way the project stores it. Empty means "no classes", not "". */
export function cssClassValue(names: string[]): string | undefined {
  const cleaned = names.filter(Boolean);
  return cleaned.length ? cleaned.join(" ") : undefined;
}

/** Add a class if it isn't there, remove it if it is. Order of the rest is kept. */
export function toggleCssClass(
  value: string | null | undefined,
  name: string,
): string | undefined {
  const names = cssClassList(value);
  const at = names.indexOf(name);
  if (at === -1) names.push(name);
  else names.splice(at, 1);
  return cssClassValue(names);
}

/**
 * The style keys a control's own feedback can write, and the CSS property each
 * one lands on. Mirrors what `applyStyle` in panel.js does with them.
 */
const FEEDBACK_STYLE_KEYS: Record<string, { prop: string; label: string }> = {
  bg_color: { prop: "background-color", label: "background" },
  text_color: { prop: "color", label: "text color" },
  border_color: { prop: "border-color", label: "border color" },
  border_width: { prop: "border-width", label: "border width" },
  border_radius: { prop: "border-radius", label: "corner radius" },
  box_shadow: { prop: "box-shadow", label: "shadow" },
  font_size: { prop: "font-size", label: "text size" },
  opacity: { prop: "opacity", label: "opacity" },
};

/** Read the look binding's per-state style objects, new shape and legacy alike. */
function feedbackStateStyles(bindings: unknown): Record<string, unknown>[] {
  const look = (bindings as { show?: { look?: unknown } } | undefined)?.show?.look as
    | {
        states?: Record<string, Record<string, unknown>>;
        style_active?: Record<string, unknown>;
        style_inactive?: Record<string, unknown>;
      }
    | undefined;
  if (!look || typeof look !== "object") return [];
  if (look.states && typeof look.states === "object") {
    return Object.values(look.states).filter((s) => s && typeof s === "object");
  }
  return [look.style_active, look.style_inactive].filter(
    (s): s is Record<string, unknown> => !!s && typeof s === "object",
  );
}

/**
 * Where a class on this element will silently take over a look it draws itself.
 *
 * The panel applies the project stylesheet at !important so that ordinary CSS
 * works without the author having to know why it wouldn't -- which means a
 * class rule now also outranks the color a control writes at runtime to show
 * state. A button that goes green when the projector is on would just stop
 * reporting, with nothing on screen to say why.
 *
 * That conflict is real either way (an author who typed !important got the same
 * result), so it is answered here, at the moment and place the class is put on
 * the element: name the class, and name what it takes over.
 */
export function feedbackClassConflicts(
  cssClass: string | null | undefined,
  css: string | null | undefined,
  bindings: unknown,
): { className: string; labels: string[] }[] {
  const applied = cssClassList(cssClass);
  if (applied.length === 0) return [];

  const states = feedbackStateStyles(bindings);
  if (states.length === 0) return [];

  const drawnByFeedback = new Map<string, string>();
  for (const state of states) {
    for (const key of Object.keys(state)) {
      const known = FEEDBACK_STYLE_KEYS[key];
      if (known) drawnByFeedback.set(known.prop, known.label);
    }
  }
  if (drawnByFeedback.size === 0) return [];

  const byClass = stylesheetClassProperties(css);
  const conflicts: { className: string; labels: string[] }[] = [];
  for (const className of applied) {
    const props = byClass.get(className);
    if (!props) continue;
    const labels = [...drawnByFeedback]
      .filter(([prop]) => props.has(prop))
      .map(([, label]) => label);
    if (labels.length > 0) conflicts.push({ className, labels });
  }
  return conflicts;
}

/**
 * Names the panel will refuse.
 *
 * The renderer adds each token with `classList.add`, which throws on a name the
 * DOM won't take -- it catches and warns, so a bad name is silently no-op'd on
 * the glass. Saying so here, while it is being typed, is the difference between
 * a two-second fix and an afternoon.
 */
export function invalidCssClassNames(value: string | null | undefined): string[] {
  return cssClassList(value).filter((name) => !VALID_CLASS_NAME.test(name));
}
