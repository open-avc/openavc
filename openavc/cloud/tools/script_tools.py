"""The AI's script tools: read a script's source and health, create one,
replace its source, delete it.

A script is a ``ScriptConfig`` in the project plus a ``.py`` file in the
project's ``scripts/`` folder. Every path is checked to stay inside that
folder, and a new file must be a plain ``.py`` name. A source is compiled
before it is written, so a syntax error is refused with nothing written; a
written source goes live (the running script is reloaded, the new version
imported before the old one is unloaded), and the answer names every handler
in it that nothing in the project can reach and every timer that stops at its
next edit: the IDE's script lint, at the other door that writes a script.

A create writes its file and a delete removes it inside the project change
(``core/change_files.py``): a change that is not applied leaves the folder
as it was, and a deleted script's file goes only once the project file no
longer names it. Every tool here runs under the assistant's own lock, which
is taken before the change door's (``_reload_lock``), never inside it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openavc.cloud.tools import ToolEditError, apply_tool_edit
from openavc.core.change_files import ChangeFiles
from openavc.core.event_references import (
    NO_EMITTER,
    PLATFORM_EVENT,
    STARTUP_TIMER,
    project_script_sources,
    script_issues,
)
from openavc.utils.paths import is_safe_script_filename, safe_path_within

#: Said once per reply that carries one, at the moment it is broken rather
#: than in the standing prompt: the lead, then one sentence per kind present.
_WARNING_NOTE_LEAD = "The write landed -- these are warnings, not failures."
_KIND_NOTES = {
    NO_EMITTER: (
        "A handler for a custom. event nothing sends can never fire, and nothing else will "
        "report it. Emit it from a macro step, a control action or another script, or listen "
        "for the event that does happen."
    ),
    STARTUP_TIMER: (
        "system.started comes once per server start, so an every() started in its handler "
        "stops at the script's next edit. Call every() at the top of the script, where each "
        "load starts it again."
    ),
    PLATFORM_EVENT: (
        "Names starting with system. are OpenAVC's own: events.emit() of one is refused when "
        "it runs, and a handler for one OpenAVC never sends never fires. Use the custom. name "
        "each warning gives."
    ),
}


def _validate_script_syntax(source: str, filename: str = "<script>") -> str | None:
    """Validate Python script syntax. Returns error string or None."""
    try:
        compile(source, filename, "exec")
    except SyntaxError as e:
        line_info = f" (line {e.lineno})" if e.lineno else ""
        return f"Python syntax error{line_info}: {e.msg}"
    return None


def _scripts_dir(engine: Any) -> Path:
    return engine.project_path.parent / "scripts"


def _find_script_in(project: Any, script_id: str) -> Any:
    return next((s for s in project.scripts if s.id == script_id), None)


def _find_script(engine: Any, script_id: str) -> Any:
    return _find_script_in(engine.project, script_id)


def _refuse_syntax_error(script_id: str, source: str, filename: str) -> None:
    """ToolEditError for a source that does not compile; an empty one is not checked."""
    if not source:
        return
    err = _validate_script_syntax(source, filename)
    if err:
        raise ToolEditError({"error": f"Script '{script_id}': {err}"})


def _new_script_path(scripts_dir: Path, filename: str) -> Path:
    """Where a new script's file goes, or ToolEditError: a plain ``.py`` name, inside ``scripts_dir``."""
    if not is_safe_script_filename(filename):
        raise ToolEditError({
            "error": f"Invalid script filename '{filename}': must be a plain .py name with no path separators",
        })
    path = safe_path_within(scripts_dir, filename)
    if path is None:
        raise ToolEditError({"error": "Invalid script filename"})
    return path


def _script_issues(engine: Any, script_id: str, source: str) -> list[dict]:
    """The script lint's issues in the script just written: handlers nothing in
    the project can reach, timers that stop at the script's next edit, and
    the platform's own event names.

    The same check the IDE's script lint runs, at the other door that writes a
    script. A generator is told in the reply it is already reading, which is
    the round trip that would otherwise be spent shipping a handler that never
    runs -- the exact shape a starter template shipped with for months.
    """
    project = getattr(engine, "project", None)
    if project is None:
        return []
    sources = project_script_sources(project, _scripts_dir(engine))
    sources[script_id] = source
    return script_issues(sources, project).get(script_id, [])


def _with_script_warnings(result: dict, engine: Any, script_id: str, source: str) -> dict:
    """``result`` with the script lint's warnings on the source just written, if any."""
    issues = _script_issues(engine, script_id, source)
    if issues:
        kinds = {issue["kind"] for issue in issues}
        result["warnings"] = [issue["message"] for issue in issues]
        result["warning_note"] = " ".join(
            [_WARNING_NOTE_LEAD, *(note for kind, note in _KIND_NOTES.items() if kind in kinds)]
        )
    return result


def _append_script(project: Any, new_script: Any) -> None:
    if any(s.id == new_script.id for s in project.scripts):
        raise ToolEditError({"error": f"Script '{new_script.id}' already exists"})
    # The file is written next: it would replace the source that script runs.
    owner = next((s for s in project.scripts if s.file == new_script.file), None)
    if owner is not None:
        raise ToolEditError({
            "error": f"The file '{new_script.file}' belongs to script '{owner.id}'. "
                     f"Give the new script a file of its own.",
        })
    project.scripts.append(new_script)


def _remove_script(project: Any, script_id: str) -> Any:
    """The script's record taken out of ``project``, or ToolEditError when it has none."""
    removed = _find_script_in(project, script_id)
    if removed is None:
        raise ToolEditError({"error": f"Script '{script_id}' not found"})
    project.scripts = [s for s in project.scripts if s.id != script_id]
    return removed


def _script_file(scripts_dir: Path, script: Any) -> Path:
    """Where ``script``'s file is, or ToolEditError for a path outside ``scripts_dir``."""
    path = safe_path_within(scripts_dir, script.file)
    if path is None:
        raise ToolEditError({"error": "Invalid script filename"})
    return path


class ScriptToolsMixin:
    """Script source, health, create, update and delete."""

    async def _get_script_source(self, input: dict) -> Any:
        script_id = input.get("script_id", "")
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        script = _find_script(engine, script_id)
        if script is None:
            return {"error": f"Script '{script_id}' not found"}
        path = safe_path_within(_scripts_dir(engine), script.file)
        if path is None:
            return {"error": "Invalid script filename"}
        if not path.exists():
            return {"error": f"Script file not found: {script.file}"}
        return {"id": script_id, "file": script.file, "source": path.read_text(encoding="utf-8")}

    async def _get_script_health(self, input: dict) -> Any:
        """Which scripts are failing right now, in the three ways they can.

        The same three stores ``GET /api/scripts/errors`` answers the Scripts
        view from, and for the same reason: "the button does nothing" is asked
        of the assistant at least as often as it is looked up in the view, and
        until this the assistant's only route to the answer was the raw log --
        which is the state that door was built to end.

        ``script_id`` narrows it to one script; without it the whole project is
        reported. A healthy project answers with three empty maps rather than
        an error, because "nothing is broken" is the answer to the question.
        """
        engine = self._get_engine()
        if not engine:
            return {"error": "No project loaded"}
        if not engine.scripts:
            return {"errors": {}, "abandoned": {}, "runtime": {}}

        health = {
            "errors": engine.scripts.get_load_errors(),
            "abandoned": engine.scripts.get_abandoned_loads(),
            "runtime": engine.scripts.get_runtime_errors(),
        }
        script_id = str(input.get("script_id", "") or "")
        if script_id:
            health = {
                kind: {script_id: found[script_id]}
                for kind, found in health.items()
                if script_id in found
            } or {"errors": {}, "abandoned": {}, "runtime": {}}
        return health

    async def _create_script(self, input: dict) -> Any:
        from openavc.core.project_loader import ScriptConfig

        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        script_id = input.get("id", "")
        filename = input.get("file", f"{script_id}.py")
        source = input.get("source", "")
        if _find_script(engine, script_id) is not None:
            return {"error": f"Script '{script_id}' already exists"}

        scripts_dir = _scripts_dir(engine)
        try:
            _refuse_syntax_error(script_id, source, filename)
            path = _new_script_path(scripts_dir, filename)
        except ToolEditError as refused:
            return refused.result
        # Built before the change: an id the project refuses is answered
        # with nothing written.
        new_script = ScriptConfig(
            id=script_id, file=filename,
            enabled=input.get("enabled", True), description=input.get("description", ""),
        )
        files = ChangeFiles()

        def mutate(project: Any) -> None:
            _append_script(project, new_script)
            files.write(path, source)

        # The scripts reconcile loads just this script, from the file the
        # change has just written.
        err = await apply_tool_edit(engine, mutate, files=files)
        if err:
            return err
        return _with_script_warnings({"status": "created", "id": script_id}, engine, script_id, source)

    async def _update_script_source(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        script_id = input.get("script_id", "")
        source = input.get("source", "")
        script = _find_script(engine, script_id)
        if script is None:
            return {"error": f"Script '{script_id}' not found"}
        try:
            _refuse_syntax_error(script_id, source, script.file)
        except ToolEditError as refused:
            return refused.result
        path = safe_path_within(_scripts_dir(engine), script.file)
        if path is None:
            return {"error": "Invalid script filename"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        # A source write alone is inert: the running script is reloaded like
        # the IDE's reload button. reload_script imports the new version before
        # unloading the old, so a broken edit leaves the previous version
        # active (reported in the result).
        result: dict[str, Any] = {"status": "saved"}
        if engine.scripts:
            result["reload"] = engine.scripts.reload_script(script.model_dump())
        return _with_script_warnings(result, engine, script_id, source)

    async def _delete_script(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        script_id = input.get("script_id", "")
        script = _find_script(engine, script_id)
        if script is None:
            return {"error": f"Script '{script_id}' not found"}
        scripts_dir = _scripts_dir(engine)
        try:
            _script_file(scripts_dir, script)
        except ToolEditError as refused:
            return refused.result

        # Read before the file goes: what is about to be stranded, in the same
        # shape delete_device / delete_macro / delete_variable hand back. The
        # delete still succeeds -- the impact is what to tell the user to fix.
        impact = self._find_references("script", script_id)
        files = ChangeFiles()

        def mutate(project: Any) -> None:
            files.remove(_script_file(scripts_dir, _remove_script(project, script_id)))

        # The scripts reconcile unloads just this script; its file goes once
        # the project file no longer names it.
        err = await apply_tool_edit(engine, mutate, files=files)
        if err:
            return err
        result: dict[str, Any] = {"status": "deleted"}
        if impact:
            result["impact"] = impact
        return result
