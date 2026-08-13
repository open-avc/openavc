import type { ProjectConfig, UIElement } from "./types";
import { request } from "./base";

/**
 * Expanding a matrix for the canvas, without a second copy of the expander.
 *
 * The Builder's canvas is an iframe of the real panel, and the panel reads
 * sources and destinations as finished lists -- it has no idea a generator
 * exists (matrix plan D6). The Builder, meanwhile, holds unsaved edits, so it
 * cannot just ask for the saved project's resolution.
 *
 * So it posts the configs it is about to draw and gets them back expanded. That
 * is a network round trip in the middle of somebody typing, which is the cost
 * D6 named up front, and this is the cache that pays it once.
 *
 * MEASURED, in Chromium against a real server, before deciding to cache:
 * 0.9ms median and 1.7ms at the 95th percentile for a page of four matrices,
 * and 1.0ms median for a single 128x128 (the biggest thing anyone authors --
 * the request carries the generator, not the 128 rows it stands for). Against a
 * 50ms debounce, that is not perceptible on loopback and the cache is not what
 * makes typing smooth.
 *
 * It is here for the case loopback does not measure. The Programmer IDE is
 * normally open on another machine and often through the cloud tunnel, where a
 * real RTT lands on every project change -- and a project change is any edit at
 * all, including dragging a box or typing in a label field, none of which can
 * move a matrix. Keyed on the config, only an edit to a matrix's own
 * configuration misses; everything else is already answered.
 */

/** A resolved matrix config, keyed by the exact config that produced it. */
const cache = new Map<string, Record<string, unknown>>();

/**
 * How many expansions to remember. A project has a handful of matrices and an
 * author edits one config at a time, so this only ever holds the trail of
 * intermediate states from one editing session. Oldest out first -- a Map keeps
 * insertion order, so the first key is the oldest.
 */
const CACHE_LIMIT = 200;

function remember(key: string, value: Record<string, unknown>): void {
  cache.set(key, value);
  while (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next();
    if (oldest.done) break;
    cache.delete(oldest.value);
  }
}

/** Every matrix element in a project, page elements and masters alike. */
function matrixElements(project: ProjectConfig): UIElement[] {
  const found: UIElement[] = [];
  for (const page of project.ui?.pages ?? []) {
    for (const el of page.elements ?? []) {
      if (el.type === "matrix") found.push(el);
    }
  }
  for (const el of project.ui?.master_elements ?? []) {
    if (el.type === "matrix") found.push(el as UIElement);
  }
  return found;
}

/**
 * A copy of the project with every matrix expanded, ready to post to the canvas.
 *
 * Returns the project unchanged when it holds no matrix, which is the common
 * case and must cost nothing. On a failed request it returns the project
 * unchanged too: an unexpanded matrix draws an empty box, which is honest about
 * the server being unreachable, and is a great deal better than the canvas
 * refusing to redraw at all.
 */
export async function resolveProjectMatrices(
  project: ProjectConfig
): Promise<ProjectConfig> {
  const elements = matrixElements(project);
  if (!elements.length) return project;

  const keys = new Map<string, string>(); // element id -> cache key
  const misses: Record<string, unknown> = {};
  for (const el of elements) {
    const key = JSON.stringify(el.matrix_config ?? null);
    keys.set(el.id, key);
    if (!cache.has(key)) misses[el.id] = el.matrix_config ?? {};
  }

  if (Object.keys(misses).length) {
    try {
      const reply = await request<{ configs: Record<string, Record<string, unknown>> }>(
        "/ui/resolve-matrix",
        { method: "POST", body: JSON.stringify({ configs: misses }) }
      );
      for (const [id, resolved] of Object.entries(reply?.configs ?? {})) {
        const key = keys.get(id);
        if (key !== undefined) remember(key, resolved);
      }
    } catch {
      return project;
    }
  }

  // Structural clone down to the elements that changed only. The canvas posts
  // this across a postMessage boundary on every edit, so a deep clone of a
  // whole project would be the expensive half of this function.
  const swap = (el: UIElement): UIElement => {
    if (el.type !== "matrix") return el;
    const resolved = cache.get(keys.get(el.id) ?? "");
    return resolved ? { ...el, matrix_config: resolved } : el;
  };
  return {
    ...project,
    ui: {
      ...project.ui,
      pages: (project.ui?.pages ?? []).map((page) => ({
        ...page,
        elements: (page.elements ?? []).map(swap),
      })),
      master_elements: (project.ui?.master_elements ?? []).map(
        (el) => swap(el as UIElement) as typeof el
      ),
    },
  };
}
