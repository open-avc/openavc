"""Mixin for AI tool handlers that read and write the project's ``ui/`` folder.

The integrator has had four doors into this folder since custom controls
shipped -- the IDE's editor, a drag-and-drop, a ``.zip`` import, and project
import or backup restore. The assistant had none, which meant the one
capability that lets a panel do something the built-in controls cannot was
also the one capability the assistant could not reach: it could point an
element at a file and never put a file there.

Two rules shape everything here, and both are somebody else's:

- **What may be stored is ``core/custom_ui.py``.** Path shape, file types, the
  size caps, containment against a symlink. Every door asks that module rather
  than re-stating a rule, which is why a control dropped in and a control
  written by the assistant are indistinguishable afterwards.
- **What will draw wrong is ``core/custom_ui_review.py``.** It warns and never
  refuses, and it never executes anything -- so a reply from here says the
  bytes landed and were read, never that the control works.

Deliberately absent from ``_CONCURRENT_SAFE_TOOLS``: everything not in that set
already takes the project lock and fires the one-time pre-AI backup, and
``create_backup`` already collects ``ui/`` recursively with its folders intact.
So the undo story for an assistant that writes a bad control is the one that
already existed, with nothing new built for it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from openavc.cloud.tools import ToolEditError, apply_tool_edit
from openavc.core.custom_ui import (
    MAX_FILE_SIZE,
    MAX_FILES,
    MAX_TOTAL_SIZE,
    UI_DIR_NAME,
    CustomUIPathError,
    iter_files,
    normalize_relpath,
    resolve_within,
    tree_totals,
)
from openavc.core.custom_ui_review import (
    review_saved_file,
    review_stylesheet,
    review_tree,
    stylesheet_class_names,
    stylesheet_class_usage,
)
from openavc.ui.page_references import custom_file_references
from openavc.utils.fileio import atomic_write_text
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: File types this tool may write. Narrower than what the folder ACCEPTS, and
#: the difference is the caller: an image, a font and a sound arrive as bytes
#: through an upload, and a tool that takes text cannot produce one. Saying so
#: is better than writing a ``.png`` full of markup and reporting it saved.
_WRITABLE_EXTENSIONS = frozenset({
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".md", ".csv", ".svg",
})

#: Types worth handing back as text. A ``.png`` answers with its size and never
#: its bytes -- there is no reason to spend a context window on an image.
_READABLE_EXTENSIONS = _WRITABLE_EXTENSIONS

#: Said once per reply that carries warnings, and the second sentence is the
#: whole point: a static review can say what a control will get wrong here, and
#: cannot say whether it works. Nothing on this path executes the markup -- the
#: instance ships no browser, and rendering it through whatever IDE happens to
#: be open would make a tool's answer depend on somebody having a tab open.
_REVIEW_NOTE = (
    "The file was written and read, not run -- nothing here executes a control. These "
    "are warnings, not failures: each names the file and what will go wrong in a room. "
    "Fix them, then look at the control on the UI Builder's design canvas (or a panel) "
    "before reporting it finished."
)

_CLEAN_NOTE = (
    "Nothing to report. The file was written and read, not run -- open the control on "
    "the UI Builder's design canvas to see it draw."
)

#: The stylesheet's own pair. It is not a control and nothing about it is
#: sandboxed, so the "not run" sentence would be noise; what matters here is
#: that a rule only lands on a control that names its class.
_CSS_WARNING_NOTE = (
    "The stylesheet was saved -- these are warnings, not failures. Every declaration in "
    "this sheet is applied !important on the panel, so a rule that hits more than it "
    "meant to wins over what the controls draw for themselves."
)

_CSS_CLEAN_NOTE = (
    "Saved. An element wears a rule by naming its class in css_class -- a class nothing "
    "names changes nothing on the glass."
)


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def _listed(names: Any) -> str:
    ordered = sorted(names)
    if not ordered:
        return "none yet"
    if len(ordered) <= 8:
        return ", ".join(ordered)
    return ", ".join(ordered[:8]) + f", and {len(ordered) - 8} more"


class CustomUIToolsMixin:
    """The project's ``ui/`` tree and its stylesheet, as tools."""

    # ===== HELPERS =====

    def _ui_dir(self, *, create: bool = False) -> Path | None:
        """The active project's ``ui/`` folder, or None when there is no project."""
        engine = self._get_engine()
        project_path = getattr(engine, "project_path", None) if engine else None
        if not project_path:
            return None
        ui_dir = Path(project_path).parent / UI_DIR_NAME
        if create:
            ui_dir.mkdir(parents=True, exist_ok=True)
        return ui_dir

    def _validated_path(self, raw: Any, *, require_extension: bool = True) -> str:
        """``core/custom_ui``'s own answer, as a tool error rather than a 400.

        The message is the module's, verbatim: an integrator refused at the
        drag-and-drop door and a model refused here are being told the same
        thing by the same rule, which is the reason that module exists.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise ToolEditError({"error": "A file path inside ui/ is required."})
        try:
            return normalize_relpath(raw, require_extension=require_extension)
        except CustomUIPathError as exc:
            raise ToolEditError({"error": str(exc)}) from exc

    def _target(self, ui_dir: Path, rel: str) -> Path:
        try:
            return resolve_within(ui_dir, rel)
        except CustomUIPathError as exc:
            raise ToolEditError({"error": str(exc)}) from exc

    def _uses_of(self, project: Any) -> list[Any]:
        """Every element and page in the project that points into ``ui/``."""
        uses: list[Any] = []
        for page in getattr(getattr(project, "ui", None), "pages", None) or []:
            for use in custom_file_references(page):
                uses.append((page, use))
        return uses

    def _findings_reply(
        self, result: dict, findings: list, *,
        warning_note: str = _REVIEW_NOTE, clean_note: str = _CLEAN_NOTE,
    ) -> dict:
        """Hand the caller its own mistakes back, in the reply it is reading.

        The note is not decoration. It is the sentence that stops a control
        being reported finished on the strength of the bytes having landed.
        """
        if findings:
            result["warnings"] = [f.message for f in findings]
            result["warning_note"] = warning_note
        else:
            result["note"] = clean_note
        return result

    # ===== READING =====

    async def _list_ui_files(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        ui_dir = self._ui_dir()
        if ui_dir is None:
            return {"error": "No project file on disk, so there is no ui/ folder."}

        # What points at each file, which is the half that makes this worth a
        # call: it is how "is this control still in use" gets answered without
        # reading the whole project a second time.
        used_by: dict[str, list[str]] = {}
        for page, use in self._uses_of(engine.project):
            where = (
                f"page '{use.holder_id}'" if use.what == "page"
                else f"element '{use.holder_id}' on page '{getattr(page, 'id', '?')}'"
            )
            used_by.setdefault(use.file, []).append(where)

        files = []
        for f in iter_files(ui_dir):
            rel = f.relative_to(ui_dir).as_posix()
            entry: dict[str, Any] = {
                "path": rel,
                "size": f.stat().st_size,
                "type": _extension(rel).lstrip(".") or "none",
                "readable": _extension(rel) in _READABLE_EXTENSIONS,
            }
            if rel in used_by:
                entry["used_by"] = used_by[rel]
            files.append(entry)

        total, count = tree_totals(ui_dir)
        result: dict[str, Any] = {
            "files": files,
            "file_count": count,
            "total_size": total,
            "max_file_size": MAX_FILE_SIZE,
            "max_total_size": MAX_TOTAL_SIZE,
            "max_files": MAX_FILES,
        }
        missing = sorted(f for f in used_by if f not in {e["path"] for e in files})
        if missing:
            result["missing"] = {f: used_by[f] for f in missing}
        if not files:
            result["note"] = (
                "The project has no custom UI files yet. write_ui_file puts one there; "
                'get_authoring_guide("custom") is how to write it.'
            )
        return result

    async def _read_ui_file(self, input: dict) -> Any:
        ui_dir = self._ui_dir()
        if ui_dir is None:
            return {"error": "No project file on disk, so there is no ui/ folder."}
        try:
            rel = self._validated_path(input.get("path"))
            target = self._target(ui_dir, rel)
        except ToolEditError as exc:
            return exc.result

        if not target.is_file():
            present = {f.relative_to(ui_dir).as_posix() for f in iter_files(ui_dir)}
            return {
                "error": f"'{rel}' is not in the project's ui/ folder. "
                         f"The files there are: {_listed(present)}."
            }
        size = target.stat().st_size
        if _extension(rel) not in _READABLE_EXTENSIONS:
            return {
                "path": rel,
                "size": size,
                "type": _extension(rel).lstrip("."),
                "note": "This file is not text, so its bytes are not returned. It is "
                        "part of the control and travels with the project.",
            }
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return {"error": f"'{rel}' could not be read as text ({exc.__class__.__name__})."}
        return {"path": rel, "size": size, "content": content}

    async def _get_project_stylesheet(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        css = engine.project.ui.custom_css or ""
        usage = stylesheet_class_usage(engine.project)
        result: dict[str, Any] = {
            "css": css,
            "classes": stylesheet_class_names(css),
            "classes_in_use": {name: holders for name, holders in sorted(usage.items())},
        }
        if not css:
            result["note"] = (
                "The project has no stylesheet yet. set_project_stylesheet writes the "
                "whole document; an element wears a rule by naming it in css_class."
            )
        return result

    async def _review_custom_ui(self, input: dict) -> Any:
        """Every custom-UI check, on demand, writing nothing.

        The peer of ``review_ui``: findings ride back on the write that caused
        them, which is no use at all for a control written thirty calls ago, or
        in a session that has since been compacted.
        """
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        ui_dir = self._ui_dir()
        if ui_dir is None:
            return {"error": "No project file on disk, so there is no ui/ folder."}

        only = input.get("path")
        findings = review_tree(ui_dir, project=engine.project)
        if only:
            scope = str(only).strip("/")
            findings = [
                f for f in findings
                if f.path == scope or f.path.startswith(scope + "/")
            ]
        sheet = review_stylesheet(
            engine.project.ui.custom_css or "",
            used=stylesheet_class_usage(engine.project),
        )

        by_file: dict[str, list[str]] = {}
        for finding in findings:
            by_file.setdefault(finding.path, []).append(finding.message)
        result: dict[str, Any] = {
            "files_checked": len({f.relative_to(ui_dir).as_posix() for f in iter_files(ui_dir)}),
            "finding_count": len(findings) + (0 if only else len(sheet)),
        }
        if by_file:
            result["files"] = by_file
        if sheet and not only:
            result["stylesheet"] = [f.message for f in sheet]
        if result["finding_count"]:
            result["status"] = "findings"
            result["note"] = _REVIEW_NOTE
        else:
            result["status"] = "clean"
            result["note"] = _CLEAN_NOTE
        return result

    # ===== WRITING =====

    async def _write_ui_file(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        ui_dir = self._ui_dir(create=True)
        if ui_dir is None:
            return {"error": "No project file on disk, so there is nowhere to put it."}

        content = input.get("content")
        if not isinstance(content, str):
            return {
                "error": "'content' is the whole file, as text. A write replaces what "
                         "was there -- read_ui_file first if you meant to change part "
                         "of it."
            }
        try:
            rel = self._validated_path(input.get("path"))
            if _extension(rel) not in _WRITABLE_EXTENSIONS:
                raise ToolEditError({
                    "error": f"'{rel}' is not a file type this tool can write. It writes "
                             f"text ({', '.join(sorted(_WRITABLE_EXTENSIONS))}); an "
                             f"image, font or sound has to be added in the IDE, and the "
                             f"control can then name it."
                })
            target = self._target(ui_dir, rel)
        except ToolEditError as exc:
            return exc.result

        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_SIZE:
            return {
                "error": f"'{rel}' is larger than the "
                         f"{MAX_FILE_SIZE // (1024 * 1024)} MB limit for a custom UI file."
            }
        existing = target if target.is_file() else None
        total, count = tree_totals(ui_dir, excluding=existing)
        if total + len(encoded) > MAX_TOTAL_SIZE:
            return {
                "error": f"That would take the project's custom UI files over the "
                         f"{MAX_TOTAL_SIZE // (1024 * 1024)} MB limit. Large media "
                         f"belongs in Assets."
            }
        if count + (0 if existing else 1) > MAX_FILES:
            return {"error": f"A project can hold at most {MAX_FILES} custom UI files."}

        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, content)
        log.info("Custom UI file written by AI: %s", rel)

        # Every door into this folder owes the panels the same frame, or a wall
        # panel keeps drawing the version from before the save.
        from openavc.api.routes.ui_files import announce_ui_files_changed
        await announce_ui_files_changed(rel)

        findings = review_saved_file(ui_dir, rel, project=engine.project)
        return self._findings_reply({
            "status": "replaced" if existing else "created",
            "path": rel,
            "size": len(encoded),
        }, findings)

    async def _delete_ui_file(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        ui_dir = self._ui_dir()
        if ui_dir is None:
            return {"error": "No project file on disk, so there is no ui/ folder."}
        try:
            rel = self._validated_path(input.get("path"), require_extension=False)
            target = self._target(ui_dir, rel)
        except ToolEditError as exc:
            return exc.result

        if not target.exists():
            present = {f.relative_to(ui_dir).as_posix() for f in iter_files(ui_dir)}
            return {
                "error": f"'{rel}' is not in the project's ui/ folder. "
                         f"The files there are: {_listed(present)}."
            }

        # A file still on a panel is not a file to delete. The element keeps
        # drawing and its box comes up empty, with nothing on the glass to say
        # why -- so this is the one refusal in the folder that is about the
        # PROJECT rather than about the path.
        still_shown = [
            (page, use) for page, use in self._uses_of(engine.project)
            if use.file == rel or use.file.startswith(rel + "/")
        ]
        if still_shown:
            who = ", ".join(sorted(
                f"page '{use.holder_id}'" if use.what == "page"
                else f"element '{use.holder_id}' on page '{getattr(page, 'id', '?')}'"
                for page, use in still_shown
            ))
            return {
                "error": f"'{rel}' is still shown by {who}. Point them at another file "
                         f"(update_ui_element / update_ui_page) or delete them first, "
                         f"then this file can go."
            }

        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        log.info("Custom UI file deleted by AI: %s", rel)
        from openavc.api.routes.ui_files import announce_ui_files_changed
        await announce_ui_files_changed(rel)
        return {"status": "deleted", "path": rel}

    async def _set_project_stylesheet(self, input: dict) -> Any:
        """Replace the whole stylesheet, because it is a document.

        The same shape as ``update_script_source`` and for the same reason
        ``custom_css`` sits beside ``pages`` rather than inside ``settings``: a
        partial merge of a document is how you get a mangled one.
        """
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        css = input.get("css")
        if not isinstance(css, str):
            return {
                "error": "'css' is the whole stylesheet, as text. It replaces what was "
                         "there -- read it with get_project_stylesheet first and send "
                         "back the whole document."
            }

        before = engine.project.ui.custom_css or ""

        def mutate(project):
            project.ui.custom_css = css

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        findings = review_stylesheet(css, used=stylesheet_class_usage(engine.project))
        return self._findings_reply(
            {
                "status": "saved",
                "size": len(css.encode("utf-8")),
                "previous_size": len(before.encode("utf-8")),
                "classes": stylesheet_class_names(css),
            },
            findings,
            warning_note=_CSS_WARNING_NOTE,
            clean_note=_CSS_CLEAN_NOTE,
        )
