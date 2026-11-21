"""A Windows rollback reads its cached installers' versions by the version rule.

The installers in ``update-cache`` are named ``OpenAVC-Setup-<version>.exe``.
Which of them is the running release, which one a rollback restores, which
one the Updates card offers and which ones the trim keeps all depend on
reading that version, and on putting two of them in order. A release
candidate is older than its release, and ``0.40.0-rc2`` and ``0.40.0rc2`` are
one version (``openavc/utils/version_rule.py``); these tests hold every
reader in ``updater/rollback.py`` to the rule's corpus.

They run on every platform: the Windows branches are reached by patching the
platform the module reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from openavc.updater import rollback
from openavc.utils.version_rule import read_version

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "version_rule_cases.json").read_text(encoding="utf-8")
)


def _file_name_safe(text: object) -> bool:
    return (
        isinstance(text, str) and bool(text) and text == text.strip()
        and len(text) < 100 and "/" not in text and "\\" not in text
    )


#: One spelling per corpus class that can be a file name, oldest first.
ASCENDING = [
    next(v for v in cls["versions"] if _file_name_safe(v)) for cls in CORPUS["ascending"]
]
#: Every spelling that can be a file name, with the class it belongs to.
SPELLINGS = [
    (rank, text)
    for rank, cls in enumerate(CORPUS["ascending"])
    for text in cls["versions"] if _file_name_safe(text)
]
UNREADABLE = [text for text in CORPUS["unreadable"] if _file_name_safe(text)]


def _installer(version: str) -> Path:
    return Path(f"OpenAVC-Setup-{version}.exe")


def _cache(data_dir: Path, *versions: str) -> Path:
    cache = data_dir / "update-cache"
    cache.mkdir(parents=True, exist_ok=True)
    for version in versions:
        (cache / _installer(version)).touch()
    return cache


class _Config:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir


def _on_windows(data_dir: Path, running: str):
    """The Windows branches, with ``data_dir`` as the data folder and
    ``running`` as the running version."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("openavc.updater.rollback.sys.platform", "win32"))
    stack.enter_context(patch("openavc.version.__version__", running))
    stack.enter_context(
        patch("openavc.system_config.get_system_config", return_value=_Config(data_dir))
    )
    return stack


# ── reading one installer's version ─────────────────────────────────────


@pytest.mark.parametrize("rank,text", SPELLINGS, ids=[repr(t) for _, t in SPELLINGS])
def test_an_installer_name_reads_as_its_version(rank, text):
    assert rollback._installer_version(_installer(text)) == read_version(ASCENDING[rank])


@pytest.mark.parametrize("text", UNREADABLE, ids=[repr(t) for t in UNREADABLE])
def test_an_installer_name_that_carries_no_version_reads_as_none(text):
    assert rollback._installer_version(_installer(text)) is None


def test_the_cache_lists_its_installers_in_the_rules_order(tmp_path):
    """Release candidates below their release, development builds below
    both, and Python's spellings listed with the rest."""
    cache = _cache(tmp_path, *reversed(ASCENDING), *UNREADABLE)
    listed = [rollback._installer_version(p) for p in rollback._cached_installers(cache)]
    assert listed == [read_version(v) for v in ASCENDING]


def test_pythons_spelling_of_a_release_candidate_is_a_cached_installer(tmp_path):
    cache = _cache(tmp_path, "0.40.0rc2", "0.41.0.dev1")
    names = [p.name for p in rollback._cached_installers(cache)]
    assert names == ["OpenAVC-Setup-0.40.0rc2.exe", "OpenAVC-Setup-0.41.0.dev1.exe"]


# ── the Updates card's target ───────────────────────────────────────────


def test_the_card_offers_the_release_not_its_release_candidate(tmp_path):
    """0.40.0-rc1 is older than 0.40.0, so with 0.41.0 running the newest
    other installer is 0.40.0's."""
    _cache(tmp_path, "0.40.0-rc1", "0.40.0", "0.41.0")
    with _on_windows(tmp_path, "0.41.0"):
        assert rollback.rollback_target_version(tmp_path) == "0.40.0"


def test_the_card_does_not_offer_the_running_release_spelled_another_way(tmp_path):
    _cache(tmp_path, "0.40.0", "0.41.0-rc1")
    with _on_windows(tmp_path, "0.41.0rc1"):
        assert rollback.rollback_target_version(tmp_path) == "0.40.0"


def test_the_card_skips_a_file_that_carries_no_version(tmp_path):
    _cache(tmp_path, "0.40.0", "0.41.0", "garbage")
    with _on_windows(tmp_path, "0.41.0"):
        assert rollback.rollback_target_version(tmp_path) == "0.40.0"


# ── whether a rollback is possible ──────────────────────────────────────


def test_only_the_running_release_spelled_another_way_is_no_rollback(tmp_path):
    _cache(tmp_path, "0.41.0rc1")
    with _on_windows(tmp_path, "0.41.0-rc1"):
        assert rollback.can_rollback(tmp_path) is False


def test_another_release_cached_is_a_rollback(tmp_path):
    _cache(tmp_path, "0.40.0rc2", "0.41.0")
    with _on_windows(tmp_path, "0.41.0"):
        assert rollback.can_rollback(tmp_path) is True


# ── the rollback itself ─────────────────────────────────────────────────


def _run_rollback(data_dir: Path, from_version: str, to_version: str) -> str | None:
    """Which installer ``_rollback_windows`` schedules, or None."""
    scheduled: list[Path] = []

    def _schedule(installer: Path, label: str) -> bool:
        scheduled.append(installer)
        return True

    with patch.object(rollback, "_launch_installer_via_scheduler", _schedule):
        rollback._rollback_windows(data_dir, from_version, to_version)
    return scheduled[0].name if scheduled else None


def test_the_rollback_finds_the_version_it_left_in_either_spelling(tmp_path):
    _cache(tmp_path, "0.39.0", "0.40.0rc2", "0.41.0")
    assert _run_rollback(tmp_path, "0.40.0-rc2", "0.41.0") == "OpenAVC-Setup-0.40.0rc2.exe"


def test_with_no_installer_for_the_version_it_left_the_newest_other_one_runs(tmp_path):
    """0.10.0 is newer than 0.9.0, though its name sorts first as text."""
    _cache(tmp_path, "0.9.0", "0.10.0", "0.11.0")
    assert _run_rollback(tmp_path, "0.8.0", "0.11.0") == "OpenAVC-Setup-0.10.0.exe"


def test_the_rollback_never_reinstalls_the_release_it_leaves(tmp_path):
    _cache(tmp_path, "0.41.0-rc1")
    assert _run_rollback(tmp_path, "0.40.0", "0.41.0rc1") is None


# ── the trim after a confirmed update ───────────────────────────────────


def test_the_trim_keeps_the_running_release_and_the_one_it_came_from(tmp_path):
    """Both named in Python's spelling, the files in the tag's."""
    cache = _cache(tmp_path, "0.39.0", "0.40.0", "0.41.0-rc1", "0.41.0-rc2")
    with patch("openavc.version.__version__", "0.41.0rc2"):
        removed = rollback.prune_installer_cache(tmp_path, "0.41.0rc1")
    assert sorted(removed) == ["OpenAVC-Setup-0.39.0.exe", "OpenAVC-Setup-0.40.0.exe"]
    assert sorted(p.name for p in cache.iterdir()) == [
        "OpenAVC-Setup-0.41.0-rc1.exe", "OpenAVC-Setup-0.41.0-rc2.exe",
    ]


def test_with_no_installer_for_the_version_it_came_from_the_trim_keeps_the_newest_other(tmp_path):
    cache = _cache(tmp_path, "0.40.0-rc1", "0.40.0", "0.41.0")
    with patch("openavc.version.__version__", "0.41.0"):
        rollback.prune_installer_cache(tmp_path, "0.39.0")
    assert sorted(p.name for p in cache.iterdir()) == [
        "OpenAVC-Setup-0.40.0.exe", "OpenAVC-Setup-0.41.0.exe",
    ]


def test_a_running_version_that_does_not_read_names_no_installer(tmp_path):
    """Then no installer is the running release's: the one it came from stays
    and the rest go, as for any other trim."""
    cache = _cache(tmp_path, "0.39.0", "0.40.0", "0.41.0")
    with patch("openavc.version.__version__", "unknown"):
        rollback.prune_installer_cache(tmp_path, "0.40.0")
    assert [p.name for p in cache.iterdir()] == ["OpenAVC-Setup-0.40.0.exe"]
