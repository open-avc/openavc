"""Tests for `simulator.validate.find_drivers` driver discovery (A31).

Before A31, find_drivers used a plain substring match for "DRIVER_INFO"
in the file's source. That picked up unrelated scripts that just mention
the symbol — `scripts/build_index.py`, docs, comments — and then the
validator reported them as broken drivers. The fix uses AST extraction
to confirm the file actually defines a top-level DRIVER_INFO assignment.
"""

from pathlib import Path

from simulator.validate import find_drivers, _is_python_driver


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_picks_up_real_python_driver(tmp_path):
    """A file with a real top-level DRIVER_INFO assignment is a driver."""
    driver = _write(tmp_path / "real.py", "DRIVER_INFO = {'id': 'real'}\n")
    found = find_drivers(driver)
    assert found == [(driver, "python")]


def test_picks_up_annotated_driver_info(tmp_path):
    """AnnAssign form `DRIVER_INFO: dict = {...}` is also a real driver."""
    driver = _write(tmp_path / "annotated.py", "DRIVER_INFO: dict = {'id': 'a'}\n")
    found = find_drivers(driver)
    assert found == [(driver, "python")]


def test_ignores_files_only_mentioning_driver_info(tmp_path):
    """Regression for A31: a script that mentions DRIVER_INFO in comments,
    docstrings, or string literals — but never assigns it at the top level —
    is NOT a driver and must be skipped.
    """
    # Comment-only mention
    comment_only = _write(
        tmp_path / "build_index.py",
        '"""Build the driver index from DRIVER_INFO blocks."""\n'
        "# Walks all DRIVER_INFO assignments to build catalog\n"
        "print('hello')\n",
    )
    # String-literal mention
    string_only = _write(
        tmp_path / "lint.py",
        'BANNED = ["DRIVER_INFO"]  # references the symbol but never defines\n',
    )
    # Locally-scoped DRIVER_INFO (not a real driver — top-level only)
    nested = _write(
        tmp_path / "nested.py",
        "def get():\n    DRIVER_INFO = {'id': 'fake'}\n    return DRIVER_INFO\n",
    )

    assert not _is_python_driver(comment_only)
    assert not _is_python_driver(string_only)
    assert not _is_python_driver(nested)


def test_directory_scan_filters_non_drivers(tmp_path):
    """End-to-end: a directory containing one real driver, one comment-
    mentioning script, and one _sim.py companion only finds the driver.
    """
    _write(tmp_path / "real_driver.py", "DRIVER_INFO = {'id': 'real'}\n")
    _write(tmp_path / "build_index.py", "# Walks DRIVER_INFO blocks\n")
    _write(tmp_path / "real_driver_sim.py", "DRIVER_INFO = {'id': 'sim'}\n")  # _sim suffix excluded

    found = find_drivers(tmp_path)
    paths = {p.name for p, _ in found}
    assert paths == {"real_driver.py"}


def test_directory_scan_handles_syntax_errors(tmp_path):
    """Files with syntax errors must be silently skipped — the validator
    shouldn't crash on a half-finished driver file mid-edit.
    """
    _write(tmp_path / "broken.py", "DRIVER_INFO = {invalid syntax here\n")
    _write(tmp_path / "valid.py", "DRIVER_INFO = {'id': 'v'}\n")

    found = find_drivers(tmp_path)
    paths = {p.name for p, _ in found}
    assert paths == {"valid.py"}


def test_yaml_drivers_still_found(tmp_path):
    """The AST change must not affect .avcdriver discovery."""
    _write(tmp_path / "y.avcdriver", "id: y\nname: Y\n")
    found = find_drivers(tmp_path)
    assert (tmp_path / "y.avcdriver", "yaml") in found
