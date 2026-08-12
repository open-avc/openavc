"""Serving author-supplied static files, and the one set of guards that does it.

Two surfaces hand the browser files somebody else wrote: a plugin's ``panel/``
directory and a project's ``ui/`` tree (custom controls). Both are reachable
without a credential, because a wall panel has none to present, so both need
the same four guards -- resolve inside the base directory, refuse a symlink,
refuse anything that isn't a regular file, and hand back a content type this
module recognises or ``application/octet-stream`` when it doesn't.

The octet-stream default is the load-bearing one: a type we don't list is a
file the browser must download rather than run, which is why ``.py`` and
``.sh`` are deliberately absent from the table below.

One copy on purpose. The guards used to live inside the plugin route; a second
hand-written copy for ``ui/`` is exactly the drift that ends with one surface
quietly missing the symlink check.

**A refusal that lands in an iframe has to be readable.** Both callers are the
``src`` of a frame on a panel, so the error body is not an API response nobody
sees -- it is the rectangle in the middle of somebody's wall. A JSON
``{"detail": ...}`` gets rendered as the frame's document, complete with
Chrome's Pretty-print checkbox, at whoever is standing in the room. When the
request is for a page (a type this module serves as ``text/html``), a miss or a
refusal answers with a small plain document instead. The status code is
unchanged -- the panel checks it and draws its own message over the frame.
"""

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse

#: Extension -> content type for files the panel fetches from a plugin or a
#: project's ``ui/`` tree. Anything absent is served as
#: ``application/octet-stream`` so a browser never executes something we did
#: not mean it to.
STATIC_CONTENT_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".m4s": "video/iso.segment",
    ".mpd": "application/dash+xml",
    ".vtt": "text/vtt",
}


def content_type_for(path: Path) -> str:
    """The content type this module will serve ``path`` as."""
    return STATIC_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


#: The body a page-shaped request gets instead of JSON. No theme variables and
#: no colours of its own beyond a grey that reads on either background -- it
#: cannot see the panel's theme (the frame has an opaque origin) and a white
#: card in a dark room is worse than a quiet one.
_FAULT_DOCUMENT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title><style>
html,body{{height:100%;margin:0;background:transparent}}
body{{display:flex;align-items:center;justify-content:center;padding:8px;
text-align:center;font:500 13px/1.4 system-ui,-apple-system,sans-serif;color:#8a8a8a}}
</style></head><body><p>{message}</p></body></html>
"""


def _is_page_request(path: Path) -> bool:
    """Would this request's answer be rendered as a document?

    Keyed off the same table that decides the content type, so a type served as
    HTML gets an HTML refusal and everything else keeps the JSON one.
    """
    return content_type_for(path) == "text/html"


def _fault_document(status_code: int, title: str, message: str) -> HTMLResponse:
    return HTMLResponse(
        _FAULT_DOCUMENT.format(title=title, message=message),
        status_code=status_code,
        # Never cached: the fix for this is putting the file back, and the
        # panel has to see that on the next render.
        headers={"Cache-Control": "no-store"},
    )


def serve_static_file(base_dir: Path, file_path: str, *, no_cache: bool = False):
    """Serve ``file_path`` from under ``base_dir``, or refuse with 403/404.

    ``no_cache`` adds a revalidate header -- used for the project's ``ui/``
    tree, where somebody is editing the file and expects a save to show up on
    the panel rather than a cached copy from ten minutes ago.

    A refusal for a page comes back as a small HTML document rather than a JSON
    error, because this is an iframe ``src`` and the body is what the room sees.
    """
    resolved = (base_dir / file_path).resolve()
    page = _is_page_request(resolved)

    # Path traversal and symlink escape.
    denied = False
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        denied = True
    if not denied and resolved.is_symlink():
        denied = True

    if denied:
        if page:
            return _fault_document(403, "Access denied", "This page cannot be opened.")
        raise HTTPException(status_code=403, detail="Access denied")

    if not resolved.is_file():
        if page:
            return _fault_document(
                404, "File not found", "This file is not in the project.",
            )
        raise HTTPException(status_code=404, detail="File not found")

    headers = {"Cache-Control": "no-cache"} if no_cache else None
    return FileResponse(resolved, media_type=content_type_for(resolved), headers=headers)
