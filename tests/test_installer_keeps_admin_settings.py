"""A Windows update keeps what an administrator set on the service, and drops
the OPENAVC_BIND every earlier installer set.

The installer re-creates the OpenAVC service at every install and update, and
used to give it the installer's environment every time, so a variable an
administrator set on the service went back to the installer's value or
disappeared. Every earlier installer also set OPENAVC_BIND=0.0.0.0, which
held Settings > Network > Accept connections from at every network and locked
it. Now install-service.bat sets no OPENAVC_BIND, and setup.iss reads the
previous service's environment before install-service.bat removes it, then
writes the administrator's variables into the new one before
start-service.bat starts it. The 0.0.0.0 earlier installers set is dropped on
the way; any other OPENAVC_BIND was set on purpose and is kept.

start-service.bat then waits for the server to listen on port 8080, on every
address or on this computer's own address, whichever the setting gives.

The first tests read the installer files, which is all a test can do with them
off Windows. The merge tests compile the installer's own [Code] and run
MergeServiceEnvironment on Windows, through the registry, which holds a value
exactly as the service's environment does; they need Inno Setup and no
elevation. The wait test runs the batch file's own findstr line against
netstat rows on Windows.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from tests import gates, inno_harness

INSTALLER = inno_harness.SETUP_ISS.parent
INSTALL_SERVICE_BAT = INSTALLER / "install-service.bat"
START_SERVICE_BAT = INSTALLER / "start-service.bat"

# The installer's defaults, which a value an administrator set replaces.
DEFAULTS = {"OPENAVC_ALLOW_ANONYMOUS"}


def _commands(text: str) -> list[str]:
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().upper().startswith(("REM", "@ECHO"))
    ]


def _setup_iss() -> str:
    return inno_harness.SETUP_ISS.read_text(encoding="utf-8")


def _section(text: str, name: str) -> list[str]:
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("["):
            inside = line.strip() == f"[{name}]"
            continue
        if inside and line.strip() and not line.startswith(";"):
            lines.append(line)
    return lines


def _procedure(text: str, header: str) -> str:
    start = text.index(header)
    return text[start:text.index("\nend;", start)]


def _environment_line() -> str:
    return next(
        line for line in _commands(INSTALL_SERVICE_BAT.read_text(encoding="utf-8"))
        if " set OpenAVC AppEnvironmentExtra " in line
    )


# -- the order the service is set up in --------------------------------------


def test_the_service_is_started_only_after_its_environment_is_put_back():
    run = _section(_setup_iss(), "Run")
    install = next(i for i, line in enumerate(run) if "install-service.bat" in line)
    start = next(i for i, line in enumerate(run) if "start-service.bat" in line)
    assert install < start
    assert "BeforeInstall: RememberServiceEnvironment" in run[install]
    assert "AfterInstall: KeepAdministratorEnvironment" in run[install]
    for line in (run[install], run[start]):
        assert "waituntilterminated" in line
        for gate in ("Components:", "Tasks:", "Check:"):
            assert gate not in line, f"a service step is gated: {line}"


def test_only_start_service_starts_the_service():
    start = '"%INSTALL_DIR%\\nssm.exe" start OpenAVC'
    assert start not in _commands(INSTALL_SERVICE_BAT.read_text(encoding="utf-8"))
    assert start in _commands(START_SERVICE_BAT.read_text(encoding="utf-8"))


def test_the_installer_ships_start_service_on_every_install():
    """Not gated on a component: Inno skips a gated file on some silent upgrades."""
    entries = [
        line for line in _section(_setup_iss(), "Files")
        if line.startswith("Source:") and '"start-service.bat"' in line
    ]
    assert len(entries) == 1
    assert 'DestDir: "{app}"' in entries[0]
    assert "Components:" not in entries[0]


# -- what the installer sets --------------------------------------------------


def test_the_installer_variables_are_the_ones_install_service_sets():
    """IsInstallerVariable names what is always the installer's; the rest of
    install-service.bat's environment is defaults an administrator can change."""
    set_by_bat = set(re.findall(r'"([A-Z_]+)=', _environment_line()))
    owned = set(re.findall(
        r"Name = '([A-Z_]+)'", _procedure(_setup_iss(), "function IsInstallerVariable("),
    ))
    assert owned == {
        "OPENAVC_DATA_DIR", "OPENAVC_LOG_DIR", "OPENAVC_PROJECT", "OPENAVC_SERVICE_MANAGED",
    }
    assert set_by_bat == owned | DEFAULTS


def test_the_installer_leaves_who_can_connect_to_settings():
    """A value on the service would hold Accept connections from and lock it."""
    assert "OPENAVC_BIND" not in _environment_line()


def test_the_bind_dropped_is_exactly_the_one_earlier_installers_set():
    """Every earlier install-service.bat wrote OPENAVC_BIND=0.0.0.0; any other
    value someone chose. The merge tests below run the comparison itself."""
    assert "EarlierInstallerBind = '0.0.0.0';" in _setup_iss()
    merge = _procedure(_setup_iss(), "function MergeServiceEnvironment(")
    assert "not IsEarlierInstallerBind(Kept[I])" in merge


# -- the wait, run by the batch file's own findstr line -----------------------

LISTENING = [
    # Accept connections from: any network
    "  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING",
    # one adapter, or this computer only: the loopback socket is always there
    "  TCP    127.0.0.1:8080         0.0.0.0:0              LISTENING",
    # netstat translates the state column; the addresses are what is matched
    "  TCP    127.0.0.1:8080         0.0.0.0:0              ABHÖREN",
]

NOT_OUR_LISTENER = [
    # the tray's and a browser's connections to the previous server
    "  TCP    127.0.0.1:8080         127.0.0.1:1433         TIME_WAIT",
    "  TCP    127.0.0.1:1425         127.0.0.1:8080         TIME_WAIT",
    "  TCP    127.0.0.1:1425         127.0.0.1:8080         ESTABLISHED",
    "  TCP    127.0.0.1:1425         127.0.0.1:8080         SYN_SENT",
    # other ports and addresses
    "  TCP    127.0.0.1:18080        0.0.0.0:0              LISTENING",
    "  TCP    0.0.0.0:18080          0.0.0.0:0              LISTENING",
    "  TCP    10.0.0.0:8080          0.0.0.0:0              LISTENING",
    "  TCP    192.168.4.49:8080      0.0.0.0:0              LISTENING",
    "  TCP    [::]:8080              [::]:0                 LISTENING",
]


@pytest.fixture(scope="module")
def wait_match(tmp_path_factory):
    """Whether start-service.bat's wait would end on these netstat rows."""
    if not sys.platform.startswith("win"):
        pytest.skip("findstr is the Windows one")
    line = next(
        line for line in _commands(START_SERVICE_BAT.read_text(encoding="utf-8"))
        if line.startswith("netstat -an | findstr ")
    )
    findstr = re.fullmatch(r"netstat -an \| (findstr .+?) >nul 2>&1", line)
    assert findstr, f"the wait's netstat line changed shape: {line}"
    folder = tmp_path_factory.mktemp("wait_match")
    script = folder / "wait_match.cmd"
    script.write_text(f'@{findstr.group(1)} < "%~1"\r\n', encoding="ascii")

    def run(rows: list[str]) -> bool:
        listing = folder / "netstat.txt"
        listing.write_bytes("".join(f"{row}\r\n" for row in rows).encode("utf-8"))
        done = subprocess.run(
            ["cmd", "/c", str(script), str(listing)], capture_output=True, timeout=30,
        )
        return done.returncode == 0

    return run


@pytest.mark.parametrize("row", LISTENING)
def test_the_wait_ends_when_the_server_listens(wait_match, row):
    assert wait_match([*NOT_OUR_LISTENER, row])


def test_the_wait_goes_on_while_only_old_connections_are_left(wait_match):
    for row in NOT_OUR_LISTENER:
        assert not wait_match([row]), row
    assert not wait_match(NOT_OUR_LISTENER)


# -- the merge, run by the installer's own code ------------------------------

KEY = r"Software\OpenAVC installer test"

FRESH = [
    r"OPENAVC_DATA_DIR=C:\ProgramData\OpenAVC",
    r"OPENAVC_LOG_DIR=C:\ProgramData\OpenAVC\logs",
    r"OPENAVC_PROJECT=C:\ProgramData\OpenAVC\projects\default\project.avc",
    "OPENAVC_ALLOW_ANONYMOUS=false",
    "OPENAVC_SERVICE_MANAGED=1",
]

# What every earlier installer left on the service.
EARLIER = [*FRESH[:3], "OPENAVC_BIND=0.0.0.0", *FRESH[3:]]


def _host_reason() -> str | None:
    if not sys.platform.startswith("win"):
        return "this is not Windows"
    if not inno_harness.iscc():
        return "Inno Setup (iscc) is not installed"
    return None


@pytest.fixture(scope="module")
def merge(tmp_path_factory):
    """MergeServiceEnvironment(previous, fresh), as the installer runs it."""
    gates.skip_or_fail(gates.HOST_FIREWALL, _host_reason())
    import winreg

    exe = inno_harness.build(
        inno_harness.code_harness(
            _setup_iss(), "OpenAVC environment check",
            [
                f"if not RegQueryMultiStringValue(HKCU, '{KEY}', 'Previous', Previous) then",
                "  Previous := '';",
                f"if not RegQueryMultiStringValue(HKCU, '{KEY}', 'Fresh', Fresh) then",
                "  Fresh := '';",
                f"RegWriteMultiStringValue(HKCU, '{KEY}', 'Merged',",
                "  MergeServiceEnvironment(Previous, Fresh));",
            ],
            variables=["Previous, Fresh: String;"],
            run_as_invoker=True,
        ),
        tmp_path_factory.mktemp("environment_check"), "environment_check",
    )

    def run(previous: list[str] | None, fresh: list[str] = FRESH) -> list[str]:
        if _key_exists():
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            winreg.SetValueEx(key, "Fresh", 0, winreg.REG_MULTI_SZ, fresh)
            if previous is not None:
                winreg.SetValueEx(key, "Previous", 0, winreg.REG_MULTI_SZ, previous)
        inno_harness.run(exe)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value, kind = winreg.QueryValueEx(key, "Merged")
        assert kind == winreg.REG_MULTI_SZ
        return value

    yield run
    if _key_exists():
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)


def _key_exists() -> bool:
    import winreg

    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY).Close()
    except OSError:
        return False
    return True


def test_a_new_install_gets_the_installer_environment(merge):
    assert merge(None) == FRESH
    assert merge([]) == FRESH


def test_an_update_of_an_untouched_service_changes_nothing(merge):
    assert merge(FRESH) == FRESH


def test_an_update_from_an_earlier_installer_drops_its_bind(merge):
    """The service an earlier installer left gets this installer's set, so
    Accept connections from is no longer locked."""
    assert merge(EARLIER) == FRESH


def test_the_earlier_bind_is_dropped_whatever_the_case_of_its_name(merge):
    """Windows matches environment names without regard to case."""
    assert merge([*FRESH, "openavc_bind=0.0.0.0"]) == FRESH


@pytest.mark.parametrize("chosen", [
    "OPENAVC_BIND=10.20.30.5",
    "OPENAVC_BIND=127.0.0.1",
    "OPENAVC_BIND=::",
    "openavc_bind=10.20.30.5",
])
def test_a_bind_someone_set_on_purpose_is_kept(merge, chosen):
    """Only the earlier installers' exact value is theirs; any other was a choice."""
    previous = [*FRESH[:3], chosen, *FRESH[3:]]
    assert merge(previous) == [*FRESH, chosen]


def test_an_update_of_a_service_already_merged_changes_nothing(merge):
    merged = [*FRESH, "OPENAVC_BIND=10.20.30.5"]
    assert merge(merged) == merged


def test_variables_the_administrator_added_are_kept_exactly(merge):
    added = [
        "HTTPS_PROXY=http://proxy.campus.example:3128",
        'OPENAVC_PROGRAMMER_PASSWORD=p%PATH%a!s"s^&|<>w=rd é',
        "OPENAVC_ALLOW_ANONYMOUS=true",
    ]
    merged = merge([*EARLIER, *added])
    assert merged == [
        *FRESH[:3], "OPENAVC_ALLOW_ANONYMOUS=true", FRESH[4], added[0], added[1],
    ]


def test_the_installer_variables_always_take_the_installer_values(merge):
    previous = [
        r"OPENAVC_DATA_DIR=D:\Elsewhere",
        r"openavc_log_dir=D:\Elsewhere\logs",
        r"OPENAVC_PROJECT=D:\Elsewhere\project.avc",
        "OPENAVC_SERVICE_MANAGED=0",
    ]
    assert merge(previous) == FRESH


def test_a_name_in_other_case_replaces_the_default(merge):
    """Windows matches environment names without regard to case."""
    merged = merge(["openavc_allow_anonymous=true"])
    assert merged == [*FRESH[:3], "openavc_allow_anonymous=true", FRESH[4]]


def test_a_default_the_service_never_had_is_still_written(merge):
    """A service without OPENAVC_ALLOW_ANONYMOUS gets the installer's value."""
    previous = [entry for entry in FRESH if not entry.startswith("OPENAVC_ALLOW_ANONYMOUS=")]
    assert merge(previous) == FRESH


def test_an_entry_that_is_not_name_equals_value_is_dropped(merge):
    assert merge(["no equals sign", "=C:=C:\\", *FRESH]) == FRESH
