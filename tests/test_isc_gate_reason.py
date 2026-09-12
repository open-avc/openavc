"""Where the ISC off switch is, said out loud.

ISC runs behind two switches: `system.json`'s `isc.enabled` and the project's
own. Either being off tears the mesh down, and that teardown is CORRECT — it
even logs cleanly. What was missing was any surface saying *which* one is down.
`GET /api/isc/status` answered `{"status": "disabled", "enabled": false}` and
the Programmer filled the silence with "ISC is enabled in the project but not
running yet. Save and reload the project to start ISC" — advice that cannot
work when the system gate is the one holding it, and that cost an hour on a
system reporting `enabled: true` from `/api/system/config` while
`/api/isc/status` said disabled, for 21 hours, after opening a template project.
"""

from unittest.mock import MagicMock

import pytest

from openavc.api.routes.isc import _why_isc_is_off


def _engine(*, system: bool, project: bool, has_project: bool = True):
    engine = MagicMock()
    engine.project = MagicMock() if has_project else None
    engine.isc_gates.return_value = (system, project)
    return engine


class TestWhichGateIsNamed:
    def test_the_system_gate_is_named_when_it_is_the_one_down(self):
        answer = _why_isc_is_off(_engine(system=False, project=True))
        assert answer["disabled_by"] == "system"
        assert "system.json" in answer["reason"]
        assert "isc.enabled" in answer["reason"]

    def test_the_project_gate_is_named_when_it_is_the_one_down(self):
        answer = _why_isc_is_off(_engine(system=True, project=False))
        assert answer["disabled_by"] == "project"
        assert "project" in answer["reason"]

    def test_both_gates_are_named_when_both_are_down(self):
        """Naming one would send the reader to flip it and find nothing
        changed, which is the same hour over again."""
        answer = _why_isc_is_off(_engine(system=False, project=False))
        assert answer["disabled_by"] == "both"
        assert "system.json" in answer["reason"]
        assert "project" in answer["reason"]

    def test_no_project_is_its_own_answer(self):
        answer = _why_isc_is_off(_engine(system=True, project=False, has_project=False))
        assert answer["disabled_by"] == "no_project"

    def test_on_everywhere_but_not_running_points_at_the_log(self):
        """`_start_isc` catches its own exceptions and logs, so a genuine
        start failure reaches here looking exactly like a switched-off mesh."""
        answer = _why_isc_is_off(_engine(system=True, project=True))
        assert answer["disabled_by"] == "error"
        assert "log" in answer["reason"]

    @pytest.mark.parametrize(
        "gates", [(False, True), (True, False), (False, False), (True, True)]
    )
    def test_every_answer_names_a_gate_and_says_something(self, gates):
        answer = _why_isc_is_off(_engine(system=gates[0], project=gates[1]))
        assert answer["disabled_by"]
        assert answer["reason"].endswith(".")


class TestTheGateReadingItself:
    """The route's answer is only as good as what it asks. Both start paths
    read the same helper, so the reported reason cannot drift from the rule
    that actually stops ISC."""

    def test_the_engine_reads_both_switches(self):
        from openavc.core.engine import Engine

        source = Engine.isc_gates.__doc__ or ""
        assert "system config" in source

    def test_no_second_copy_of_the_gate_expression(self):
        from pathlib import Path

        engine_src = (
            Path(__file__).resolve().parents[1] / "openavc" / "core" / "engine.py"
        ).read_text(encoding="utf-8")
        # The expression used to be spelled at both start paths. It lives in
        # the helper now and nowhere else; a second copy is how the reported
        # reason and the rule that actually stops ISC would part.
        assert engine_src.count('get("isc", "enabled"') == 1
        assert engine_src.count("self.isc_gates()") == 2
