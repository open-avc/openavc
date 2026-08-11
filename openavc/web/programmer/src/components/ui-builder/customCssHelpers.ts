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

/**
 * Every class name the stylesheet defines, in the order it first mentions them.
 *
 * Only selectors are read -- the text before a `{`. Declarations inside a rule
 * are skipped, which is what keeps `border-radius: 0.5rem` from being offered
 * as a class called `5rem`. An at-rule prelude (`@media (min-width: 37.5rem)`)
 * is skipped for the same reason; the rules nested inside it are read normally,
 * so a class that only exists in a media query still shows up.
 */
export function stylesheetClassNames(css: string | null | undefined): string[] {
  if (!css || typeof css !== "string") return [];
  const text = stripComments(css);
  const found: string[] = [];
  const seen = new Set<string>();

  let buffer = "";
  for (const ch of text) {
    if (ch === "{" || ch === "}" || ch === ";") {
      if (ch === "{" && !buffer.trimStart().startsWith("@")) {
        const prelude = blankStringsAndAttributes(buffer);
        for (const match of prelude.matchAll(CLASS_IN_SELECTOR)) {
          const name = match[1];
          if (!seen.has(name)) {
            seen.add(name);
            found.push(name);
          }
        }
      }
      buffer = "";
      continue;
    }
    buffer += ch;
  }
  return found;
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
