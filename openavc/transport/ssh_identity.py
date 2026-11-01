"""OpenAVC's own SSH key: the identity key sign-in uses when a device names none.

The SSH transport signs in with a key or a password. A key has to be a file
the ``ssh`` client can read, and on several install types nobody can put a
file on the server: there is no shell to copy one into, or the person doing
the setup has only the Programmer. So the server makes one key pair of its own
the first time it is needed, keeps it in ``{data_dir}/ssh/`` beside the
``known_hosts`` file the transport already uses there, and shows the public
half in the Programmer for the integrator to add to the device's account.
A device whose Key Path is set keeps using that file.

The key is RSA. AV and network gear that takes user keys at all takes RSA;
Ed25519 and ECDSA user keys are refused by older device SSH stacks, and a
switch CLI that accepts a key by pasting it often knows no other type. 3072
bits is OpenSSH's own default size. Signatures still use SHA-2 wherever the
device offers it; the transport adds ``ssh-rsa`` (SHA-1) only as a fallback
for devices that offer nothing newer.

The private key never leaves this module except as a path handed to ``ssh``:
it is not logged and no route returns it. Its file is readable by the server's
own account only. On POSIX that is mode 0600. On Windows, the OpenSSH client
refuses a private key any other account can read ("UNPROTECTED PRIVATE KEY
FILE"), and a data folder outside the installer's (a source checkout, a
custom ``OPENAVC_DATA_DIR``) can grant every local user read access, so the
file stops inheriting its folder's permissions and is granted to SYSTEM,
Administrators and the account the server runs as, each named by SID so the
command works in every Windows language.

Creation is safe against two devices connecting at once, in one process or
two: the key is written to a private temporary file and hard-linked into
place, which fails if another writer got there first; the loser discards its
copy and uses the winner's.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from openavc.utils.logger import get_logger
from openavc.utils.spawn import CREATE_NO_WINDOW

log = get_logger(__name__)

KEY_TYPE = "ssh-rsa"
KEY_BITS = 3072
KEY_FILENAME = "id_openavc"

_lock = threading.Lock()
# (path, mtime_ns, size) -> PlatformKey, so the Programmer's reads and every
# connect do not re-parse the key file.
_cache: dict[tuple[str, int, int], "PlatformKey"] = {}


@dataclass(frozen=True)
class PlatformKey:
    """The platform key's private file and the public half people see."""

    path: Path
    public_key: str
    """One OpenSSH ``authorized_keys`` line: type, base64 blob, comment."""
    fingerprint: str
    """``SHA256:<base64>``, the form ``ssh-keygen -l`` and most devices show."""
    key_type: str
    bits: int


def key_dir() -> Path:
    from openavc.system_config import get_data_dir

    return get_data_dir() / "ssh"


def key_path() -> Path:
    return key_dir() / KEY_FILENAME


def ensure_platform_key(directory: Path | None = None) -> PlatformKey:
    """Return the platform key, making it first if it does not exist yet.

    Blocking (RSA generation takes a moment on small ARM boards), so async
    callers run it in a thread.
    """
    target_dir = Path(directory) if directory is not None else key_dir()
    target = target_dir / KEY_FILENAME
    with _lock:
        if not target.exists():
            _create(target)
        return _load(target)


def _load(path: Path) -> PlatformKey:
    from cryptography.hazmat.primitives import serialization

    st = path.stat()
    cache_key = (str(path), st.st_mtime_ns, st.st_size)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    private = serialization.load_ssh_private_key(path.read_bytes(), password=None)
    public = private.public_key()
    line = public.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    key_type, blob_b64 = line.split()[:2]
    digest = hashlib.sha256(base64.b64decode(blob_b64)).digest()
    fingerprint = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
    bits = getattr(public, "key_size", 0) or 0
    key = PlatformKey(
        path=path,
        public_key=f"{key_type} {blob_b64} {_comment()}",
        fingerprint=fingerprint,
        key_type=key_type,
        bits=bits,
    )
    _cache.clear()
    _cache[cache_key] = key
    return key


def _comment() -> str:
    """``openavc@<this computer's name>``, so a device's key list says whose it is.

    Kept to the characters every device CLI takes in a pasted key line: no
    spaces, which some parsers read as the end of the key.
    """
    try:
        host = socket.gethostname().split(".")[0]
    except OSError:
        host = ""
    host = re.sub(r"[^A-Za-z0-9_-]", "", host)
    if not host or host.lower() == "localhost":
        return "openavc"
    return f"openavc@{host}"


def _create(target: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(target.parent, stat.S_IRWXU)
        except OSError as exc:
            log.debug("Could not set 0700 on %s: %s", target.parent, exc)

    private = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )

    tmp = target.with_name(f".{KEY_FILENAME}.{os.getpid()}.{threading.get_ident()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        _restrict(tmp)
        try:
            os.link(tmp, target)
        except FileExistsError:
            # Another process made it between our exists() check and now.
            return
        except OSError:
            # A filesystem without hard links: claim the name exclusively.
            if not target.exists():
                os.replace(tmp, target)
                log.info("Created OpenAVC's SSH key at %s", target)
            return
        log.info("Created OpenAVC's SSH key at %s", target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("Could not remove %s: %s", tmp, exc)


def _restrict(path: Path) -> None:
    """Make the key readable by the server's own account only."""
    if os.name == "posix":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            log.warning("Could not set 0600 mode on %s: %s", path, exc)
        return
    if sys.platform != "win32":
        return
    sid = _current_user_sid()
    if not sid:
        # Without our own SID, removing inherited access could lock this
        # process out of its own key. Leave it as the folder grants it.
        log.warning(
            "Could not read this account's SID; %s keeps its folder's permissions",
            path,
        )
        return
    if not _set_exact_dacl(path, ("SY", "BA", sid)):
        log.warning("Could not restrict access to %s", path)


def _set_exact_dacl(path: Path, trustees: tuple[str, ...]) -> bool:
    """Replace the file's access list with full control for ``trustees`` only,
    and stop it inheriting from the folder.

    Windows' OpenSSH refuses a private key any other account can reach. An
    icacls /inheritance:r /grant:r pass left an OWNER RIGHTS entry that it
    refused (CI's Windows runner); setting the whole list at once through the
    security API leaves nothing behind to refuse.
    """
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sddl = "D:P" + "".join(f"(A;;FA;;;{who})" for who in dict.fromkeys(trustees))
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        ctypes.c_wchar_p(sddl), 1, ctypes.byref(descriptor), None,
    ):
        log.debug("SDDL %s refused: error %s", sddl, ctypes.get_last_error())
        return False
    try:
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted),
        ):
            return False
        se_file_object = 1
        dacl_security_information = 0x00000004
        protected_dacl_security_information = 0x80000000
        advapi32.SetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        result = advapi32.SetNamedSecurityInfoW(
            str(path), se_file_object,
            dacl_security_information | protected_dacl_security_information,
            None, None, dacl, None,
        )
        if result != 0:
            log.debug("SetNamedSecurityInfoW on %s: error %s", path, result)
        return result == 0
    finally:
        kernel32.LocalFree(descriptor)


def _current_user_sid() -> str | None:
    try:
        result = subprocess.run(
            [_system32("whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"S-1-[0-9-]+", result.stdout or "")
    return match.group(0) if match else None


def _system32(exe: str) -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    candidate = os.path.join(root, "System32", exe)
    return candidate if os.path.exists(candidate) else exe
