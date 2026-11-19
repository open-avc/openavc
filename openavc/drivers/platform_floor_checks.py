"""The platform release a driver's fields need, against the one it declares.

Every field the platform grew carries the release that grew it (``since`` in
``spec``'s registry, read by ``spec.platform_requirements``), so the floor a
driver needs is computed, and ``platform_version_errors`` says when its
``min_platform_version`` is lower. The author's doors refuse it
(``avcdriver_semantic.check_driver_definition``, which re-exports this);
publishing additionally requires the declaration.

A declared floor is read by the version rule (``utils/version_rule.py``), as
the install gate reads it, so a release candidate is older than its release:
a driver declaring ``0.40.0-rc1`` does not cover a field 0.40.0 grew.

Purity contract: standard library plus ``spec`` and the version rule;
vendored into openavc-drivers and openavc-sdk with the definition walk.
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.spec import platform_requirements
from openavc.utils.version_rule import read_version


# How many offending fields a min_platform_version message names before it
# stops listing. Enough to see the pattern; the author only has to fix the
# declaration once regardless of how many fields forced it.
_MAX_NAMED_REQUIREMENTS = 4


def _requirement_summary(
    requirements: list[tuple[str, str]], floor: str
) -> tuple[str, str]:
    """The offending field list, and how to refer back to it in the sentence."""
    names = [where for where, version in requirements if version == floor]
    shown = ", ".join(names[:_MAX_NAMED_REQUIREMENTS])
    rest = len(names) - _MAX_NAMED_REQUIREMENTS
    if rest > 0:
        return f"{shown} (+{rest} more)", "those fields"
    return shown, "that field" if len(names) == 1 else "those fields"


def platform_version_errors(
    driver_def: dict[str, Any],
    *,
    require_declaration: bool = False,
    tier: str = "yaml",
) -> list[str]:
    """Check ``min_platform_version`` against the fields the driver uses.

    Every field the platform grew carries the release that grew it (``since``
    in the registry), so what a driver needs is computable rather than
    remembered. Declaring less than that is not a lint: the install gate
    honours the declaration, so the driver installs on a release that reads
    the file, ignores the fields it does not know, and runs wrong — a push
    subscription that never arms, a command whose framing is dropped.

    ``require_declaration`` additionally rejects a driver that uses a gated
    field and declares nothing at all. Off by default: a locally authored
    driver has no install gate to satisfy and is being written against the
    platform it is sitting on. On for publishing, where the field is what
    stops the driver reaching someone running an older release.

    ``tier`` is the driver's format ("yaml" or "python"); a few floors bind
    one format only (``spec.platform_requirements``).
    """
    if not isinstance(driver_def, dict):
        return []
    requirements = platform_requirements(driver_def, tier)
    if not requirements:
        return []
    floor = requirements[0][1]
    declared = driver_def.get("min_platform_version")

    named, them = _requirement_summary(requirements, floor)
    if declared is None or declared == "":
        if not require_declaration:
            return []
        return [
            f"min_platform_version is not declared, but this driver uses "
            f"{named}, which needs platform {floor}. Without it the driver "
            f"installs on older releases that ignore {them}. Add "
            f'min_platform_version: "{floor}".'
        ]

    parsed = read_version(declared)
    if parsed is None:
        return [
            f"min_platform_version must be a version number like "
            f'"{floor}" (got {declared!r})'
        ]
    if parsed >= read_version(floor):
        return []
    return [
        f'min_platform_version is "{declared}", but this driver uses {named}, '
        f'which needs platform {floor}. Raise it to "{floor}", or drop '
        f"{them} if the driver has to keep installing on {declared}."
    ]
