"""
The Windows installer's signature is checked before an update runs it.

An update on Windows hands the downloaded installer to Task Scheduler, which
runs it as SYSTEM. These tests pin that nothing reaches the scheduler unless
the installer carries OpenAVC LLC's valid Authenticode signature, on both doors
that apply an update: the cloud-pushed one (apply_cloud_update) and the
GitHub-discovered one (apply_update).

Everywhere but the last class, Windows' answer is stubbed (read_signature
returns a report), so the rules run on every platform. The last class runs the
real PowerShell check on Windows against a signed program already on the
machine, a copy with one byte changed, and a copy with its signature removed.
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openavc.updater import installer_signature as sig
from openavc.updater.installer_signature import (
    InstallerSignatureError,
    SignatureUnreadable,
    check_installer,
    read_signature,
    signature_problem,
)

# What Windows reports for a release installer (subject and issuer exactly as
# Get-AuthenticodeSignature writes them for OpenAVC-Setup-0.38.0.exe).
GOOD = {
    "status": "Valid",
    "message": "Signature verified.",
    "subject": "CN=OpenAVC LLC, O=OpenAVC LLC, L=Lillington, S=North Carolina, C=US",
    "issuer": "CN=Microsoft ID Verified CS AOC CA 03, O=Microsoft Corporation, C=US",
    "thumbprint": "3AAB8595A990D8D5A5A0195FAB78280EF2E267F2",
}
UNSIGNED = {
    "status": "NotSigned",
    "message": "The file is not digitally signed.",
    "subject": "", "issuer": "", "thumbprint": "",
}
TAMPERED = {**GOOD, "status": "HashMismatch",
            "message": "The contents of the file may have been tampered with."}
WRONG_SIGNER = {
    **GOOD,
    "subject": "CN=Acme Widgets Inc, O=Acme Widgets Inc, L=Springfield, S=Oregon, C=US",
    "issuer": "CN=Some Public CA Code Signing CA R1, O=Some Public CA, C=US",
}


# ===========================================================================
# The rules: which reports are refused, and what the user is told
# ===========================================================================


class TestSignatureProblem:

    def test_release_installer_passes(self):
        assert signature_problem(GOOD) is None

    def test_either_microsoft_issuing_ca_passes(self):
        # Releases alternate between issuing CAs; the CA is not pinned.
        report = {**GOOD, "issuer": GOOD["issuer"].replace("AOC CA 03", "AOC CA 04")}
        assert signature_problem(report) is None

    def test_city_is_not_pinned(self):
        report = {**GOOD, "subject": GOOD["subject"].replace("Lillington", "Raleigh")}
        assert signature_problem(report) is None

    def test_state_written_as_st_passes(self):
        report = {**GOOD, "subject": GOOD["subject"].replace("S=", "ST=")}
        assert signature_problem(report) is None

    def test_unsigned_is_refused(self):
        assert signature_problem(UNSIGNED) == "The downloaded installer is not signed."

    def test_tampered_is_refused(self):
        assert signature_problem(TAMPERED) == (
            "The downloaded installer was changed after it was signed."
        )

    @pytest.mark.parametrize("status", ["NotTrusted", "UnknownError", "Incompatible", ""])
    def test_any_other_status_is_refused(self, status):
        problem = signature_problem({**GOOD, "status": status})
        assert problem and problem.startswith("Windows does not trust")

    def test_valid_signature_from_another_publisher_is_refused(self):
        problem = signature_problem(WRONG_SIGNER)
        assert problem == (
            f"The downloaded installer is signed by {WRONG_SIGNER['subject']}, "
            "not by OpenAVC LLC."
        )

    @pytest.mark.parametrize("subject", [
        # Same name, another state: another legal entity.
        "CN=OpenAVC LLC, O=OpenAVC LLC, S=Delaware, C=US",
        # Name in the CN only.
        "CN=OpenAVC LLC, O=Acme Widgets Inc, S=North Carolina, C=US",
        # The pinned fields smuggled inside one quoted value.
        'CN="Acme, O=OpenAVC LLC, S=North Carolina, C=US", O=Acme, S=Oregon, C=US',
        # A pinned field written twice.
        "CN=Acme, CN=OpenAVC LLC, O=OpenAVC LLC, S=North Carolina, C=US",
        "",
    ])
    def test_near_miss_subjects_are_refused(self, subject):
        problem = signature_problem({**GOOD, "subject": subject})
        assert problem and "not by OpenAVC LLC" in problem

    @pytest.mark.parametrize("issuer", [
        "CN=Some Public CA Code Signing CA R1, O=Some Public CA, C=US",
        "CN=Microsoft ID Verified CS AOC CA 03, O=Acme Widgets Inc, C=US",
        "CN=Acme ID Verified CS AOC CA 03, O=Microsoft Corporation, C=US",
        "",
    ])
    def test_our_name_from_another_issuer_is_refused(self, issuer):
        problem = signature_problem({**GOOD, "issuer": issuer})
        assert problem and problem.endswith("not by Microsoft.")


class TestCheckInstaller:

    def test_good_signature_returns_the_report(self, tmp_path):
        with patch.object(sig, "read_signature", return_value=GOOD):
            assert check_installer(tmp_path / "x.exe", "1.2.3") == GOOD

    def test_refusal_says_nothing_ran_and_where_to_get_the_installer(self, tmp_path):
        with patch.object(sig, "read_signature", return_value=UNSIGNED):
            with pytest.raises(InstallerSignatureError) as exc:
                check_installer(tmp_path / "x.exe", "1.2.3")
        assert str(exc.value) == (
            "The downloaded installer is not signed. Nothing was installed. To update, "
            "download OpenAVC-Setup-1.2.3.exe from "
            "https://github.com/open-avc/openavc/releases and run it on this computer."
        )

    def test_a_check_that_cannot_run_refuses(self, tmp_path):
        with patch.object(sig, "read_signature",
                          side_effect=SignatureUnreadable("Windows PowerShell was not found")):
            with pytest.raises(InstallerSignatureError) as exc:
                check_installer(tmp_path / "x.exe", "1.2.3")
        message = str(exc.value)
        assert message.startswith(
            "The downloaded installer's signature could not be checked "
            "(Windows PowerShell was not found). Nothing was installed. Try the update again"
        )


# ===========================================================================
# Asking Windows: the PowerShell child, with subprocess faked
# ===========================================================================


def _completed(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class TestReadSignature:

    def test_parses_the_report_and_passes_the_path_out_of_band(self, tmp_path):
        target = tmp_path / "OpenAVC Setup's copy.exe"
        with patch.object(sig.subprocess, "run",
                          return_value=_completed(json.dumps(GOOD).encode())) as run:
            assert read_signature(target) == GOOD

        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        assert Path(argv[0]).parts[-3:] == ("WindowsPowerShell", "v1.0", "powershell.exe")
        assert "-EncodedCommand" not in argv
        # The file reaches the script through the environment, never the
        # command line, so no path is ever parsed as PowerShell.
        assert kwargs["env"]["OPENAVC_INSTALLER_PATH"] == str(target)
        assert not any(str(target) in a for a in argv)
        assert kwargs["creationflags"] == sig.CREATE_NO_WINDOW
        assert kwargs["timeout"] == sig.TIMEOUT_SECONDS

    def test_unsigned_file_has_empty_signer_fields(self, tmp_path):
        out = json.dumps({"status": "NotSigned", "message": "The file is not digitally signed.",
                          "subject": "", "issuer": "", "thumbprint": ""}).encode()
        with patch.object(sig.subprocess, "run", return_value=_completed(out)):
            assert read_signature(tmp_path / "x.exe") == UNSIGNED

    def test_the_script_is_one_argument_without_double_quotes(self):
        assert '"' not in sig._SCRIPT and "\n" not in sig._SCRIPT

    @pytest.mark.parametrize("raised, reason", [
        (FileNotFoundError(), "Windows PowerShell was not found"),
        (subprocess.TimeoutExpired(cmd="powershell", timeout=120), "did not finish within 120 seconds"),
        (PermissionError("access denied"), "could not be started: access denied"),
    ])
    def test_no_answer_is_unreadable(self, tmp_path, raised, reason):
        with patch.object(sig.subprocess, "run", side_effect=raised):
            with pytest.raises(SignatureUnreadable, match=reason):
                read_signature(tmp_path / "x.exe")

    def test_powershell_7_module_folders_are_not_inherited(self, tmp_path, monkeypatch):
        # Started from PowerShell 7, Windows PowerShell 5.1 inherits 7's module
        # folders and cannot load the module Get-AuthenticodeSignature is in.
        monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\7\Modules")
        with patch.object(sig.subprocess, "run",
                          return_value=_completed(json.dumps(GOOD).encode())) as run:
            read_signature(tmp_path / "x.exe")
        assert not any(k.upper() == "PSMODULEPATH" for k in run.call_args.kwargs["env"])

    def test_powershell_error_is_unreadable_with_its_whole_message(self, tmp_path):
        # PowerShell wraps the message at the console width, then says where.
        err = (
            b"Get-AuthenticodeSignature : The 'Get-AuthenticodeSignature' command was found in the module\r\n"
            b"'Microsoft.PowerShell.Security', but the module could not be loaded.\r\n"
            b"At line:1 char:34\r\n"
            b"+ ... ; $s = Get-AuthenticodeSignature -LiteralPath $env:OPENAVC_INSTALL ...\r\n"
        )
        with patch.object(sig.subprocess, "run", return_value=_completed(stderr=err, returncode=1)):
            with pytest.raises(SignatureUnreadable) as exc:
                read_signature(tmp_path / "x.exe")
        assert str(exc.value) == (
            "Get-AuthenticodeSignature : The 'Get-AuthenticodeSignature' command was found in "
            "the module 'Microsoft.PowerShell.Security', but the module could not be loaded."
        )

    def test_powershell_error_with_nothing_on_stderr_names_the_exit_code(self, tmp_path):
        with patch.object(sig.subprocess, "run", return_value=_completed(returncode=3)):
            with pytest.raises(SignatureUnreadable, match="exited with code 3"):
                read_signature(tmp_path / "x.exe")

    @pytest.mark.parametrize("stdout", [b"", b"not json", b"[1, 2]", b'"Valid"'])
    def test_output_that_is_not_a_report_is_unreadable(self, tmp_path, stdout):
        with patch.object(sig.subprocess, "run", return_value=_completed(stdout)):
            with pytest.raises(SignatureUnreadable, match="no signature report"):
                read_signature(tmp_path / "x.exe")


# ===========================================================================
# Both update doors: nothing is scheduled without a good signature
# ===========================================================================

INSTALLER_BYTES = b"MZ fake installer payload"
INSTALLER_SHA = hashlib.sha256(INSTALLER_BYTES).hexdigest()


def _windows_manager(tmp_path):
    from openavc.updater.manager import UpdateManager
    from openavc.updater.platform import DeploymentType

    mgr = UpdateManager.__new__(UpdateManager)
    mgr._data_dir = tmp_path / "data"
    mgr._data_dir.mkdir()
    mgr._project_path = mgr._data_dir / "project.avc"
    mgr._state = MagicMock()
    mgr._checker = MagicMock()
    mgr._update_in_progress = False
    mgr._history = []
    mgr._deployment_type = DeploymentType.WINDOWS_INSTALLER
    state: dict = {}
    mgr._state.set = lambda key, value, source="system": state.__setitem__(key, value)
    mgr.state = state
    return mgr


def _write_installer(mgr, filename):
    cache = mgr._data_dir / "update-cache"
    cache.mkdir(exist_ok=True)
    path = cache / filename
    path.write_bytes(INSTALLER_BYTES)
    mgr.downloaded = path
    return path


async def _cloud_update(mgr):
    async def fake_download(url, filename):
        return _write_installer(mgr, filename)

    mgr._download_artifact = fake_download
    return await mgr.apply_cloud_update(
        target_version="1.0.0",
        update_url="https://github.com/open-avc/openavc/releases/download/v1.0.0/OpenAVC-Setup-1.0.0.exe",
        checksum_sha256=INSTALLER_SHA,
    )


async def _github_update(mgr):
    from openavc.updater.checker import ReleaseInfo

    mgr._checker.last_result = ReleaseInfo(
        version="1.0.0", tag="v1.0.0", prerelease=False, changelog="",
        published_at="2026-11-01T00:00:00Z", assets=[],
    )

    async def fake_download_update(release):
        return _write_installer(mgr, f"OpenAVC-Setup-{release.version}.exe")

    mgr._download_update = fake_download_update
    return await mgr.apply_update()


DOORS = {"cloud-pushed": _cloud_update, "github-discovered": _github_update}


async def _run_door(mgr, door, report):
    with patch("openavc.updater.manager.can_self_update", return_value=True), \
         patch("openavc.updater.manager.__version__", "0.9.0"), \
         patch("openavc.updater.backup.create_backup", return_value=mgr._data_dir / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.updater.rollback._launch_installer_via_scheduler",
               return_value=True) as scheduler, \
         patch.object(sig, "read_signature", return_value=report) as reader, \
         patch.object(mgr, "_restart_process") as restart, \
         patch.object(mgr, "_save_history"):
        result = await DOORS[door](mgr)
    return result, scheduler, reader, restart


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(DOORS))
@pytest.mark.parametrize("report, reason", [
    (UNSIGNED, "is not signed"),
    (WRONG_SIGNER, "not by OpenAVC LLC"),
    (TAMPERED, "was changed after it was signed"),
])
async def test_refused_installer_is_never_scheduled(tmp_path, door, report, reason):
    from openavc.updater.rollback import read_pending_marker

    mgr = _windows_manager(tmp_path)
    result, scheduler, reader, restart = await _run_door(mgr, door, report)

    assert result["success"] is False
    assert reason in result["error"]
    reader.assert_called_once_with(mgr.downloaded)
    scheduler.assert_not_called()
    restart.assert_not_called()
    # Deleted, so a later rollback cannot find it in the cache and run it.
    assert not mgr.downloaded.exists()
    assert mgr.state["system.update_status"] == "error"
    assert reason in mgr.state["system.update_error"]
    assert "Nothing was installed" in mgr.state["system.update_error"]
    assert mgr._history[0]["status"] == "failed"
    assert read_pending_marker(mgr._data_dir) is None
    assert mgr._update_in_progress is False


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(DOORS))
async def test_good_signature_is_scheduled(tmp_path, door):
    mgr = _windows_manager(tmp_path)
    result, scheduler, reader, restart = await _run_door(mgr, door, GOOD)

    assert result["success"] is True
    reader.assert_called_once_with(mgr.downloaded)
    scheduler.assert_called_once_with(mgr.downloaded, "1.0.0")
    assert mgr.downloaded.exists()
    assert mgr.state.get("system.update_error", "") == ""
    assert mgr._history[0]["status"] == "pending"


# ===========================================================================
# The real check, on Windows
# ===========================================================================


def _security_directory(data: bytes) -> tuple[int, int, int]:
    """(offset of the directory entry, certificate table offset, its size)."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0"
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    entry = optional + (96 if magic == 0x10B else 112) + 4 * 8
    offset, size = struct.unpack_from("<II", data, entry)
    return entry, offset, size


def _signed_program() -> Path:
    """A program on this machine that carries its own Authenticode signature.

    The python.org interpreter is signed by the Python Software Foundation,
    which makes it a valid signature from a publisher that is not OpenAVC LLC.
    """
    candidates = [getattr(sys, "_base_executable", ""), sys.executable]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            data = Path(candidate).read_bytes()
            if _security_directory(data)[2]:
                return Path(candidate)
    pytest.fail(
        f"None of {candidates} carries an embedded signature. These tests need "
        "the python.org interpreter, which is signed."
    )


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows' own signature check")
class TestOnWindows:

    def test_signed_program_from_another_publisher(self):
        program = _signed_program()
        report = read_signature(program)
        assert report["status"] == "Valid", report
        assert sig._dn_fields(report["subject"]).get("CN"), report
        with pytest.raises(InstallerSignatureError, match="not by OpenAVC LLC"):
            check_installer(program, "1.0.0")

    def test_one_byte_changed_is_tampered(self, tmp_path):
        data = bytearray(_signed_program().read_bytes())
        _, table, _ = _security_directory(data)
        flip = table // 2
        data[flip] ^= 0xFF
        copy = tmp_path / "tampered.exe"
        copy.write_bytes(bytes(data))

        assert read_signature(copy)["status"] == "HashMismatch"
        with pytest.raises(InstallerSignatureError, match="changed after it was signed"):
            check_installer(copy, "1.0.0")

    def test_signature_removed_is_unsigned(self, tmp_path):
        data = bytearray(_signed_program().read_bytes())
        entry, table, _ = _security_directory(data)
        struct.pack_into("<II", data, entry, 0, 0)
        copy = tmp_path / "unsigned.exe"
        copy.write_bytes(bytes(data[:table]))

        assert read_signature(copy)["status"] == "NotSigned"
        with pytest.raises(InstallerSignatureError, match="is not signed"):
            check_installer(copy, "1.0.0")

    def test_missing_file_is_unreadable(self, tmp_path):
        with pytest.raises(SignatureUnreadable):
            read_signature(tmp_path / "nowhere.exe")
