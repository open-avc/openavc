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
"""

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

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


def serve_static_file(base_dir: Path, file_path: str, *, no_cache: bool = False):
    """Serve ``file_path`` from under ``base_dir``, or raise 403/404.

    ``no_cache`` adds a revalidate header -- used for the project's ``ui/``
    tree, where somebody is editing the file and expects a save to show up on
    the panel rather than a cached copy from ten minutes ago.
    """
    resolved = (base_dir / file_path).resolve()

    # Path traversal and symlink escape.
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if resolved.is_symlink():
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    headers = {"Cache-Control": "no-cache"} if no_cache else None
    return FileResponse(resolved, media_type=content_type_for(resolved), headers=headers)
