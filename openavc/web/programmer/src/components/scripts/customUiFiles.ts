/**
 * What the Code view needs to know about a file in the project's `ui/` folder.
 *
 * Deliberately NOT a second copy of the write rules. What may be written into
 * the tree — path shape, depth, allowed types, size caps — is decided once, on
 * the server, in `core/custom_ui.py`, and its refusal message is what the
 * integrator reads. Duplicating that here would give two answers to the same
 * question and one of them would go stale.
 *
 * What is genuinely a front-end decision is which of those files an editor can
 * open: a `.png` and a `.woff` belong to a control but there is nothing to type
 * into them. That split lives here, and `tests/test_custom_ui_editor_types.py`
 * pins it against the server's list so a newly allowed type has to be
 * classified rather than silently landing in whichever half is the default.
 */

/** Extensions the editor opens as text, mapped to their Monaco language. */
export const TEXT_UI_LANGUAGES: Record<string, string> = {
  ".html": "html",
  ".htm": "html",
  ".css": "css",
  ".js": "javascript",
  ".mjs": "javascript",
  ".json": "json",
  ".svg": "xml",
  ".txt": "plaintext",
  ".md": "markdown",
  ".csv": "plaintext",
};

/** Everything else a control may carry: real files, nothing to edit. */
export const BINARY_UI_EXTENSIONS = [
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
  ".woff", ".woff2", ".ttf", ".otf",
  ".mp3", ".wav", ".ogg", ".mp4", ".webm", ".vtt",
];

export function extensionOf(path: string): string {
  const name = path.slice(path.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? "" : name.slice(dot).toLowerCase();
}

/** The Monaco language for a path, or null when it is not text. */
export function languageForUiPath(path: string): string | null {
  return TEXT_UI_LANGUAGES[extensionOf(path)] ?? null;
}

export function isEditableUiPath(path: string): boolean {
  return languageForUiPath(path) !== null;
}

/** The top folder of a path — one control is one folder, so this groups them. */
export function controlFolderOf(path: string): string {
  const cut = path.indexOf("/");
  return cut === -1 ? "" : path.slice(0, cut);
}

/**
 * Group the flat listing the API returns the way an author thinks about it:
 * one entry per control folder, loose files at the top under "".
 * Folders sort first, then loose files, each alphabetically.
 */
export function groupUiFiles<T extends { path: string }>(files: T[]): { folder: string; files: T[] }[] {
  const groups = new Map<string, T[]>();
  for (const f of files) {
    const folder = controlFolderOf(f.path);
    const bucket = groups.get(folder);
    if (bucket) bucket.push(f);
    else groups.set(folder, [f]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => {
      if (a === b) return 0;
      if (a === "") return 1;
      if (b === "") return -1;
      return a.localeCompare(b);
    })
    .map(([folder, entries]) => ({
      folder,
      files: entries.sort((x, y) => x.path.localeCompare(y.path)),
    }));
}

const CONTROL_SKELETON = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; height: 100%; font-family: sans-serif; }
  body {
    display: flex; flex-direction: column; gap: 8px;
    align-items: center; justify-content: center;
    background: var(--panel-surface, #222);
    color: var(--panel-text, #eee);
    border-radius: var(--panel-border-radius, 6px);
  }
  button {
    padding: 10px 16px; border: none; border-radius: inherit; cursor: pointer;
    background: var(--panel-button-bg, #444);
    color: var(--panel-button-text, #fff);
    font-size: 1rem;
  }
</style>
</head>
<body>
  <div id="readout">--</div>
  <button id="go">Do the thing</button>

<script>
  // Report your own errors: nothing outside this window can see them, and a
  // wall panel has no console to check.
  window.onerror = (message) => {
    parent.postMessage({ type: 'openavc:error', message: String(message) }, '*');
  };

  const readout = document.getElementById('readout');

  window.addEventListener('message', (e) => {
    const msg = e.data;

    if (msg.type === 'openavc:init') {
      // Follow the project's theme instead of fighting it.
      for (const [name, value] of Object.entries(msg.theme || {})) {
        document.documentElement.style.setProperty(name, value);
      }
      // msg.config  what you typed into "Settings passed to the control"
      // msg.state   every key this control was granted, right now
      // msg.grant   what it may reach
      // msg.edit    true while drawing in the Builder's design canvas
      readout.textContent = msg.edit ? 'Sample' : (msg.state['device.my_device.power'] ?? '--');
    }

    if (msg.type === 'openavc:state') {
      // msg.key, msg.value -- only keys this control was granted arrive here.
      readout.textContent = msg.value;
    }
  });

  document.getElementById('go').onclick = () => {
    parent.postMessage({
      type: 'openavc:action', action: 'device.command',
      device: 'my_device', command: 'power_on',
    }, '*');
  };
</script>
</body>
</html>
`;

/**
 * What a newly created file starts with.
 *
 * An empty HTML file is a blank box with nothing to react to, and the first
 * thing anybody needs is the two message directions wired up correctly. The
 * other types start with a comment naming what they are for, because a new
 * empty file in a tree is otherwise indistinguishable from a broken one.
 */
export function starterUiContent(path: string): string {
  switch (extensionOf(path)) {
    case ".html":
    case ".htm":
      return CONTROL_SKELETON;
    case ".css":
      return "/* Styles for this control. Loaded with a relative path from your page. */\n";
    case ".js":
    case ".mjs":
      return "// Script for this control. Loaded with a relative path from your page.\n";
    case ".json":
      return "{\n}\n";
    default:
      return "";
  }
}
