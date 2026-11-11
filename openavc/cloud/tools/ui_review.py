"""What the AI is told about a page it wrote, or asked about.

Every UI write answers for what it did, in the reply the caller is already
reading. The page review measures the controls the write touched
(``ui/page_review.py``: what will draw wrong, and a bound it fills in where the
driver declares one and the caller said nothing). The reference check follows
their bindings (``ui/page_references.py``: what they point at, and whether it
is there). A custom control's markup is read for what fails in this
environment (``core/custom_ui_review.py``). ``review_ui`` asks the same of the
whole project on demand, read-only.

So a page is reviewed one way for both: with the masters that draw under it,
the theme its sliders are measured against, the driver's declared ranges and
the lookups ``ui_lookups`` builds. They differ only where the review must not
write: it fills nothing in, reads the whole page rather than what one call
touched, and leaves the markup review to the write that set a grant (and to
``review_custom_ui``).

:class:`WriteNotes` is the rest of what a write says back: the warnings, the
bounds it filled in, and a grant change said out loud.
"""

from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from typing import Any

from openavc.cloud.tools.ui_lookups import (
    longest_first,
    range_lookup,
    reference_lookups,
)
from openavc.ui.page_references import reference_findings
from openavc.ui.page_review import review_master_element, review_page
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: What the warnings are for, said once per reply that carries any. It lives
#: here rather than in the standing prompt because a rule is only worth its
#: tokens at the moment it is broken.
WARNING_NOTE = (
    "The write landed -- these are warnings, not failures. Each one names the element, "
    "what will draw wrong, and the number to change. Fix them before reporting this "
    "page finished."
)

_CLEAN_NOTE = (
    "Nothing to report: every control has a box it fits in, no two collide, "
    "and every binding points at something that exists."
)


def grant_echo(before: Any, after: Any) -> dict:
    """A grant change, said out loud rather than folded into "updated".

    The grant is the entire reach model for markup somebody else wrote -- the
    panel enforces it and nothing else can -- and it is the one field on an
    element that nobody can see afterwards without reading the project back. So
    a reply that changes one names what it was and what it is now. The AI may
    set a grant on create and on update, which leaves the Builder's **Can
    reach** section the only other place this shows.
    """
    def shown(grant: Any) -> dict | None:
        if grant is None:
            return None
        dump = getattr(grant, "model_dump", None)
        return dump() if callable(dump) else dict(grant)

    return {"before": shown(before), "after": shown(after)}


class WriteNotes:
    """What a UI write says back besides its status, gathered inside its mutate.

    The review's warnings and the bounds it filled in, under the note that says
    what they are for; and a grant the write set, named before and after (or,
    for a batch of new controls, what each may reach). Kept apart from the
    status because the mutate can still be refused after a review ran: only a
    write that landed hands these back.
    """

    def __init__(self) -> None:
        self.findings: list = []
        self.adjustments: list = []
        self.grant: dict = {}
        self.grants: dict = {}

    def add(self, findings: list, adjustments: list = ()) -> None:
        self.findings.extend(findings)
        self.adjustments.extend(adjustments)

    def created(self, grant: Any) -> None:
        """A grant on a page or master this write created."""
        if grant is not None:
            self.grant.update(grant_echo(None, grant))

    def created_on(self, element_id: str, grant: Any) -> None:
        """What one control of a batch this write created may reach."""
        if grant is not None:
            self.grants[element_id] = grant_echo(None, grant)["after"]

    def changed(self, before: Any, after: Any) -> None:
        """A grant this write changed: what it was and what it is now."""
        self.grant.update(grant_echo(before, after))

    @contextmanager
    def assigning(self, target: Any, input: dict):
        """An update's fields assigned to ``target``, and its grant said back
        before and after when the call named one.

        The code that assigns an update's fields runs inside this, so no door
        has to remember the grant it started with. A refusal raised inside
        says nothing: only a write that landed hands its notes back.
        """
        before = target.grant
        yield
        if "grant" in input:
            self.changed(before, target.grant)

    def reply(self, result: dict) -> dict:
        """The write's reply, with what it has to say added after its status."""
        if self.grant:
            result["grant"] = self.grant
        if self.grants:
            result["grants"] = self.grants
        if self.adjustments:
            result["auto_filled"] = [a.message for a in self.adjustments]
        if self.findings:
            result["warnings"] = [f.message for f in self.findings]
            result["warning_note"] = WARNING_NOTE
        return result


def project_theme(project: Any) -> dict | None:
    """The project's own theme overrides a slider is measured against.

    Only one control minimum moves with the theme (a slider's thumb), and only
    ``thumb_size`` in ``element_defaults`` moves it. A custom theme FILE that
    changed it would not be seen here -- reading it would mean disk access on a
    write path -- but every built-in theme ships the 44px default, so the
    override in the project is the case that can actually differ.
    """
    try:
        overrides = project.ui.settings.theme_overrides or {}
        defaults = (overrides.get("element_defaults") or {}).get("slider")
        return defaults if isinstance(defaults, dict) else None
    except Exception:  # pragma: no cover - defensive
        return None


def master_findings(project: Any, master: Any) -> list:
    """What a master element will draw wrong, against the project's theme."""
    return review_master_element(master, project_theme(project))


class UIReview:
    """The page review, the reference check and the masters' review, asked of one
    project with one set of lookups.

    Built once per write and once per whole-project review, so the ``ui/``
    folder is listed once and each bound key's declared range is asked of its
    driver once, however many pages or controls read it. Both are built on
    first use: a review that refuses a page it cannot find asks nothing.
    """

    def __init__(self, engine: Any, devices: Any, project: Any) -> None:
        self.engine = engine
        self.devices = devices
        self.project = project

    @cached_property
    def device_ids(self) -> list[str]:
        return longest_first(self.project)

    @cached_property
    def lookups(self) -> dict[str, Any]:
        return reference_lookups(self.engine, self.devices, self.project, self.device_ids)

    def write(self, page: Any, touched: set[str], *, ranges: bool = True) -> tuple[list, list]:
        """Measure what a write just did to a page, and fix what is unambiguous.

        Runs inside the mutate, so the fills land in the project being written
        and the findings describe what actually got stored rather than what was
        asked for. Scoped to ``touched``: a call answers for the elements it
        wrote, not for everything already on the page.

        ``ranges=False`` for an edit that says nothing about a control's range
        -- filling in a bound the caller did not mention would be a surprise on
        an unrelated update, so a fill happens only where no intent was
        expressed.
        """
        findings, adjustments = self._page(
            page, range_lookup(self.devices, self.device_ids) if ranges else None,
            touched=touched,
        )
        findings.extend(custom_markup_findings(self.engine, page, touched))
        if findings:
            log.info(
                "AI UI write on page '%s': %d element(s) reviewed, %d warning(s), %d filled in",
                getattr(page, "id", "?"), len(touched), len(findings), len(adjustments),
            )
        return findings, adjustments

    def whole(self, only: Any) -> dict:
        """Every page (or the one named) and every master, reviewed; read-only.

        The checks all existed; there was no way to ASK. Findings rode back
        only on the write that caused them, so a panel built over sixty calls
        left its warnings scattered through a transcript, and a compacted or
        resumed session lost them outright. Unscoped on purpose: no
        ``touched`` filter, every arrangement. It fills nothing in -- an
        auto-fill needs a write to belong to, and answering a question should
        not change the project -- so a range mismatch is reported and never
        repaired. One page named leaves the masters out.
        """
        pages = [p for p in self.project.ui.pages if not only or p.id == only]
        if only and not pages:
            return {
                "error": f"UI page '{only}' not found. The pages are: "
                         f"{', '.join(p.id for p in self.project.ui.pages) or '(none)'}"
            }
        by_page = self._pages(pages)
        masters = [] if only else [
            f.message for master in self.project.ui.master_elements
            for f in master_findings(self.project, master)
        ]
        result: dict = {
            "pages_checked": [p.id for p in pages],
            "masters_checked": 0 if only else len(self.project.ui.master_elements),
            "finding_count": sum(map(len, by_page.values())) + len(masters),
        }
        return _whole_reply(result, by_page, masters)

    def _pages(self, pages: list) -> dict[str, list[str]]:
        """Each page's findings by page id, for the pages that have any."""
        declared_range = range_lookup(self.devices, self.device_ids)
        by_page: dict[str, list[str]] = {}
        for page in pages:
            findings, _ = self._page(page, declared_range, set_field=lambda *_: None)
            if findings:
                by_page[page.id] = [f.message for f in findings]
        return by_page

    def _page(
        self, page: Any, declared_range: Any, *,
        touched: set[str] | None = None, set_field: Any = setattr,
    ) -> tuple[list, list]:
        """One page reviewed and its references followed, the one way both ask.

        The masters draw under the page, and the page does not know they exist
        -- without them a control laid over the nav bar is silence.
        """
        findings, adjustments = review_page(
            page,
            touched=touched,
            theme=project_theme(self.project),
            declared_range=declared_range,
            set_field=set_field,
            masters=self.project.ui.master_elements,
        )
        findings.extend(reference_findings(page, touched=touched, **self.lookups))
        return findings, adjustments


def _whole_reply(result: dict, by_page: dict, masters: list) -> dict:
    """The review's counts, then what it found, then whether that is clean."""
    if by_page:
        result["pages"] = by_page
    if masters:
        result["master_elements"] = masters
    found = result["finding_count"]
    result["status"] = "findings" if found else "clean"
    result["note"] = WARNING_NOTE if found else _CLEAN_NOTE
    return result


def custom_markup_findings(engine: Any, page: Any, touched: set[str]) -> list:
    """What the markup behind a custom control on this page will get wrong.

    The other half of a UI write that touches a ``custom`` element: the element
    can be perfectly placed and perfectly granted while the page it points at
    loads a script off the internet, never listens for ``openavc:init``, or was
    granted a device it never names. That last one is the reason this runs at
    the element door at all rather than only at the file door -- an over-grant
    is created by the write that sets the grant, and the file it is about was
    written some other time.

    A file that is not there is already answered, once, by the
    dangling-reference check; saying "it has no bridge" about a file nobody
    wrote would be three warnings for one mistake. Advisory to the last: a
    folder that cannot be read says nothing rather than turning a working write
    into a warning about the disk.
    """
    try:
        from openavc.core.custom_ui_review import review_control

        uses = _reviewed_uses(page, touched)
        sources = _ui_sources(engine) if uses else None
        if not sources:
            return []
        return [
            finding
            for use in uses if use.file in sources
            for finding in review_control(
                use.file, sources,
                holder=f"{use.what} '{use.holder_id}'",
                granted=use.granted,
            )
        ]
    except Exception:  # pragma: no cover - advisory path only
        log.debug("Could not review the markup behind a custom control", exc_info=True)
        return []


def _reviewed_uses(page: Any, touched: set[str]) -> list:
    """The page's custom files this write answers for.

    The page's own markup is not one of its elements, so it is not scoped by
    ``touched`` -- the same rule the dangling-file check next to it follows.
    """
    from openavc.ui.page_references import custom_file_references

    return [
        use for use in custom_file_references(page)
        if use.what == "page" or use.holder_id in touched
    ]


def _ui_sources(engine: Any) -> dict | None:
    """The project's ``ui/`` folder read, or None with no project file or no folder."""
    from openavc.core.custom_ui import UI_DIR_NAME
    from openavc.core.custom_ui_review import tree_sources

    project_path = getattr(engine, "project_path", None)
    if not project_path:
        return None
    ui_dir = Path(project_path).parent / UI_DIR_NAME
    return tree_sources(ui_dir) if ui_dir.is_dir() else None
