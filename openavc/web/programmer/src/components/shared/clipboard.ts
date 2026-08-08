// Copy text to the clipboard, working on plain-HTTP deployments.
//
// navigator.clipboard only exists in a secure context (HTTPS or
// localhost), and the IDE ships with HTTPS off — reaching it from another
// machine over http://host:8080 is the normal Pi / mini-PC / Docker LAN
// setup. There the Clipboard API is simply undefined, so every caller
// must go through this helper, which falls back to the selection-based
// copy that works everywhere. Resolves true only when a copy actually
// happened; callers show their "Copied!" feedback on true and their
// error state (if any) on false.
export async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Permission denied or document unfocused — try the fallback.
    }
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    // Keep it out of view without scrolling the page to it.
    textarea.style.position = "fixed";
    textarea.style.top = "-1000px";
    document.body.appendChild(textarea);

    // execCommand copies the current selection, so select the textarea —
    // then put the user's selection back.
    const selection = document.getSelection();
    const previousRange =
      selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
    textarea.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(textarea);
    if (selection && previousRange) {
      selection.removeAllRanges();
      selection.addRange(previousRange);
    }
    return copied;
  } catch {
    return false;
  }
}
