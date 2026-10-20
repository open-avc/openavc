"""Integrity policy for community artifacts (drivers, plugins, companions).

The point of these tests is the *shape* of the policy, not the hashing: that a
refusal happens before anything is written, that an absent manifest degrades to
a warning while a present one is fail-closed, and that the two ways a manifest
can be worked around — an extra file, a dropped file — are both closed.
"""

from __future__ import annotations

import hashlib

import pytest

from openavc.utils.community_integrity import (
    DRIVERS_OWNER_REPO,
    PLUGINS_OWNER_REPO,
    ArtifactHashes,
    ArtifactMismatch,
    CatalogCheck,
    CommunityArtifactError,
    catalog_relpath,
    validate_catalog_url,
)

RAW = "https://raw.githubusercontent.com"


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Source pinning --------------------------------------------------------


def test_official_catalog_url_accepted():
    validate_catalog_url(
        f"{RAW}/open-avc/openavc-drivers/main/audio/acme_widget.avcdriver",
        owner_repo=DRIVERS_OWNER_REPO,
    )


def test_any_other_github_repo_refused():
    # The whole point: "is it GitHub?" is not a source check. A .py driver is
    # imported and registered, so an attacker's own repo on the same host must
    # not pass.
    with pytest.raises(CommunityArtifactError, match="path under"):
        validate_catalog_url(
            f"{RAW}/attacker/evil-drivers/main/audio/acme_widget.py",
            owner_repo=DRIVERS_OWNER_REPO,
        )


def test_lookalike_owner_prefix_refused():
    # "open-avc/openavc-drivers-evil" starts with the same characters but is a
    # different repo; the required prefix ends in a slash so it can't match.
    with pytest.raises(CommunityArtifactError):
        validate_catalog_url(
            f"{RAW}/open-avc/openavc-drivers-evil/main/audio/x.py",
            owner_repo=DRIVERS_OWNER_REPO,
        )


def test_wrong_catalog_repo_refused():
    # A real OpenAVC repo, but not the one that publishes drivers.
    with pytest.raises(CommunityArtifactError):
        validate_catalog_url(
            f"{RAW}/open-avc/openavc-plugins/main/utility/x.py",
            owner_repo=DRIVERS_OWNER_REPO,
        )


def test_plaintext_http_refused():
    with pytest.raises(CommunityArtifactError, match="https"):
        validate_catalog_url(
            f"http://raw.githubusercontent.com/{DRIVERS_OWNER_REPO}/main/audio/x.py",
            owner_repo=DRIVERS_OWNER_REPO,
        )


def test_non_github_host_refused():
    with pytest.raises(CommunityArtifactError, match="not allowed"):
        validate_catalog_url(
            f"https://evil.example.com/{DRIVERS_OWNER_REPO}/main/audio/x.py",
            owner_repo=DRIVERS_OWNER_REPO,
        )


def test_percent_encoded_traversal_refused():
    # Decoded before the prefix check, so an encoded '..' can't pose as the
    # catalog and then resolve elsewhere on the host.
    with pytest.raises(CommunityArtifactError):
        validate_catalog_url(
            f"{RAW}/open-avc/openavc-drivers/main/%2e%2e/%2e%2e/other/x.py",
            owner_repo=DRIVERS_OWNER_REPO,
        )


def test_api_host_uses_its_own_prefix():
    validate_catalog_url(
        f"https://api.github.com/repos/{PLUGINS_OWNER_REPO}/contents/utility/x",
        owner_repo=PLUGINS_OWNER_REPO,
    )
    with pytest.raises(CommunityArtifactError):
        # Missing the /repos/ prefix the API host requires.
        validate_catalog_url(
            f"https://api.github.com/{PLUGINS_OWNER_REPO}/contents/utility/x",
            owner_repo=PLUGINS_OWNER_REPO,
        )


# --- Mapping a URL back to its catalog key ---------------------------------


def test_catalog_relpath_strips_owner_repo_and_ref():
    assert catalog_relpath(
        f"{RAW}/open-avc/openavc-drivers/main/audio/acme_widget.avcdriver",
        owner_repo=DRIVERS_OWNER_REPO,
    ) == "audio/acme_widget.avcdriver"


def test_catalog_relpath_handles_nested_paths():
    assert catalog_relpath(
        f"{RAW}/open-avc/openavc-plugins/main/utility/acme/panel/app.js",
        owner_repo=PLUGINS_OWNER_REPO,
    ) == "utility/acme/panel/app.js"


def test_catalog_relpath_rejects_a_foreign_repo():
    assert catalog_relpath(
        f"{RAW}/attacker/evil/main/audio/x.py", owner_repo=DRIVERS_OWNER_REPO
    ) is None


def test_catalog_relpath_rejects_a_non_raw_host():
    assert catalog_relpath(
        f"https://api.github.com/repos/{DRIVERS_OWNER_REPO}/contents/audio/x.py",
        owner_repo=DRIVERS_OWNER_REPO,
    ) is None


# --- Hash checking ---------------------------------------------------------


def test_matching_bytes_pass():
    data = b"id: acme_widget\n"
    h = ArtifactHashes("Driver 'acme_widget'", {"audio/w.avcdriver": _h(data)}, source="cat")
    h.check("audio/w.avcdriver", data)
    assert h.verified == {"audio/w.avcdriver"}


def test_altered_bytes_refused():
    data = b"id: acme_widget\n"
    h = ArtifactHashes("Driver 'acme_widget'", {"audio/w.avcdriver": _h(data)}, source="cat")
    with pytest.raises(CommunityArtifactError, match="does not match"):
        h.check("audio/w.avcdriver", data + b"# backdoor\n")


def test_hash_comparison_is_case_insensitive():
    # Catalogs emit lowercase hex; a hand-edited uppercase entry should still
    # match rather than refusing a legitimate artifact.
    data = b"payload"
    h = ArtifactHashes("Plugin 'acme'", {"utility/acme/p.py": _h(data).upper()}, source="cat")
    h.check("utility/acme/p.py", data)


def test_unlisted_file_refused():
    # The "extra file" case. Per-file hashes alone don't cover it: a file the
    # manifest never mentions has no hash to fail, so it has to be refused for
    # being unlisted.
    h = ArtifactHashes("Plugin 'acme'", {"utility/acme/p.py": _h(b"x")}, source="cat")
    with pytest.raises(CommunityArtifactError, match="not listed"):
        h.check("utility/acme/backdoor.py", b"import os")


def test_unplaceable_file_refused_when_hashes_exist():
    # A file whose URL can't be mapped to a catalog key can't be checked, so
    # with a manifest in force it is refused rather than waved through.
    h = ArtifactHashes("Plugin 'acme'", {"utility/acme/p.py": _h(b"x")}, source="cat")
    with pytest.raises(CommunityArtifactError, match="cannot place"):
        h.check(None, b"x")


def test_absent_manifest_warns_and_proceeds(caplog):
    # The one permissive case, and it is deliberate: catalogs that predate the
    # hashes would otherwise become uninstallable.
    h = ArtifactHashes("Driver 'acme_widget'", None, source="the community driver catalog")
    assert not h.declared
    with caplog.at_level("WARNING"):
        h.check("audio/w.avcdriver", b"anything")
        h.check(None, b"anything")
    assert "no file hashes" in caplog.text


def test_absent_manifest_warns_only_once(caplog):
    # A directory plugin has dozens of files; one warning per install, not one
    # per file, or the real message drowns.
    h = ArtifactHashes("Plugin 'acme'", {}, source="cat")
    with caplog.at_level("WARNING"):
        for i in range(5):
            h.check(f"utility/acme/f{i}.py", b"x")
    assert caplog.text.count("no file hashes") == 1


def test_empty_manifest_is_treated_as_absent():
    # Not as "this plugin has no files", which would silently accept anything.
    h = ArtifactHashes("Plugin 'acme'", {}, source="cat")
    assert not h.declared


def test_non_string_manifest_entries_ignored():
    # A malformed catalog entry must not become a hash object that compares
    # oddly; it is dropped, leaving the honest "no hashes" state.
    h = ArtifactHashes("Plugin 'acme'", {"a.py": 5, 7: "abc"}, source="cat")
    assert not h.declared


# --- Completeness ----------------------------------------------------------


def test_complete_delivery_passes():
    a, b = b"one", b"two"
    h = ArtifactHashes(
        "Plugin 'acme'",
        {"utility/acme/a.py": _h(a), "utility/acme/b.py": _h(b)},
        source="cat",
    )
    h.check("utility/acme/a.py", a)
    h.check("utility/acme/b.py", b)
    h.require_complete()


def test_dropped_file_refused():
    # The other way around an "every file is hashed" check: deliver fewer files
    # than the manifest lists and every one that arrives still verifies.
    a, b = b"one", b"two"
    h = ArtifactHashes(
        "Plugin 'acme'",
        {"utility/acme/a.py": _h(a), "utility/acme/b.py": _h(b)},
        source="cat",
    )
    h.check("utility/acme/a.py", a)
    with pytest.raises(CommunityArtifactError, match="never delivered"):
        h.require_complete()


def test_completeness_is_a_no_op_without_a_manifest():
    ArtifactHashes("Plugin 'acme'", None, source="cat").require_complete()


# --- One install, one catalog ----------------------------------------------
#
# The catalog and the files it hashes are cached separately, so for a few
# minutes after an update they can disagree with nothing wrong. A disagreement
# fetches the catalog once more; it never relaxes the check.

REFUSAL = "This driver was updated a few minutes ago and cannot be installed yet."


class _Catalog:
    """A catalog that answers each fetch from a queue of file maps."""

    def __init__(self, *copies):
        self.copies = list(copies)
        self.calls: list[bool] = []

    async def load(self, force: bool) -> ArtifactHashes:
        self.calls.append(force)
        files = self.copies.pop(0) if len(self.copies) > 1 else self.copies[0]
        return ArtifactHashes("Driver 'acme'", files, source="cat")


def _start(catalog: _Catalog) -> "CatalogCheck":
    import asyncio
    return asyncio.run(CatalogCheck.fetch(catalog.load, refusal=REFUSAL))


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_mismatch_listed_and_unlisted_are_the_refetchable_refusals():
    h = ArtifactHashes("Driver 'acme'", {"audio/acme.py": _h(b"one")}, source="cat")
    with pytest.raises(ArtifactMismatch):
        h.check("audio/acme.py", b"two")
    with pytest.raises(ArtifactMismatch):
        h.check("audio/other.py", b"one")
    # A file that can't be placed is a URL problem, not a stale catalog.
    with pytest.raises(CommunityArtifactError) as exc:
        h.check(None, b"one")
    assert not isinstance(exc.value, ArtifactMismatch)


def test_a_match_never_fetches_the_catalog_again():
    catalog = _Catalog({"audio/acme.py": _h(b"new")})
    check = _start(catalog)
    _run(check.check("audio/acme.py", b"new"))
    assert catalog.calls == [False]


def test_a_stale_catalog_is_fetched_again_and_the_file_installs():
    catalog = _Catalog({"audio/acme.py": _h(b"old")}, {"audio/acme.py": _h(b"new")})
    check = _start(catalog)
    _run(check.check("audio/acme.py", b"new"))
    assert catalog.calls == [False, True]


def test_a_disagreement_that_survives_the_refetch_is_refused_in_words(caplog):
    catalog = _Catalog({"audio/acme.py": _h(b"old")})
    check = _start(catalog)
    with pytest.raises(CommunityArtifactError) as exc:
        _run(check.check("audio/acme.py", b"new"))
    assert str(exc.value) == REFUSAL
    # The hashes are for the log, not the person who pressed Install.
    assert _h(b"old") in caplog.text and _h(b"new") in caplog.text


def test_the_catalog_is_fetched_again_once_per_install_not_per_file():
    catalog = _Catalog(
        {"audio/acme.py": _h(b"old"), "audio/acme_sim.py": _h(b"sim")},
        {"audio/acme.py": _h(b"new"), "audio/acme_sim.py": _h(b"sim2")},
    )
    check = _start(catalog)
    _run(check.check("audio/acme.py", b"new"))
    with pytest.raises(CommunityArtifactError, match="cannot be installed yet"):
        _run(check.check("audio/acme_sim.py", b"sim"))
    assert catalog.calls == [False, True]


def test_files_accepted_before_the_refetch_must_match_the_new_copy_too():
    # The main file matched the first copy, the companion only matches the
    # second. Installing both would mix two versions of one driver.
    catalog = _Catalog(
        {"audio/acme.py": _h(b"v1"), "audio/acme_sim.py": _h(b"sim1")},
        {"audio/acme.py": _h(b"v2"), "audio/acme_sim.py": _h(b"sim2")},
    )
    check = _start(catalog)
    _run(check.check("audio/acme.py", b"v1"))
    with pytest.raises(CommunityArtifactError, match="cannot be installed yet"):
        _run(check.check("audio/acme_sim.py", b"sim2"))


def test_a_new_file_the_stale_catalog_does_not_list_installs_after_the_refetch():
    catalog = _Catalog(
        {"audio/acme.py": _h(b"v2")},
        {"audio/acme.py": _h(b"v2"), "audio/acme_sim.py": _h(b"sim")},
    )
    check = _start(catalog)
    _run(check.check("audio/acme.py", b"v2"))
    _run(check.check("audio/acme_sim.py", b"sim"))
    check.require_complete()


def test_a_refetch_with_no_hashes_never_turns_a_refusal_into_an_install():
    catalog = _Catalog({"audio/acme.py": _h(b"old")}, None)
    check = _start(catalog)
    with pytest.raises(CommunityArtifactError, match="cannot be installed yet"):
        _run(check.check("audio/acme.py", b"tampered"))
    assert check.hashes.declared


def test_completeness_after_a_refetch_reads_the_new_copy():
    a, b = b"one", b"two"
    catalog = _Catalog(
        {"utility/acme/a.py": _h(b"old")},
        {"utility/acme/a.py": _h(a), "utility/acme/b.py": _h(b)},
    )
    check = _start(catalog)
    _run(check.check("utility/acme/a.py", a))
    with pytest.raises(CommunityArtifactError, match="never delivered"):
        check.require_complete()
