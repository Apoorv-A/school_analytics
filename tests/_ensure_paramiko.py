"""Install a minimal paramiko stub when the package is not installed locally."""

from __future__ import annotations

import sys
from types import ModuleType


def ensure_paramiko() -> None:
    try:
        import paramiko  # noqa: F401
    except ImportError:
        pass
    else:
        return

    mod = ModuleType("paramiko")

    class SSHException(Exception):
        pass

    class RejectPolicy:
        pass

    class SSHClient:
        def set_missing_host_key_policy(self, _policy: object) -> None:
            return None

        def connect(self, **_kwargs: object) -> None:
            return None

        def open_sftp(self) -> object:
            raise SSHException("stub SSHClient")

        def close(self) -> None:
            return None

    class _KeyBase:
        @classmethod
        def from_private_key(cls, _file: object, password: object = None) -> object:
            raise SSHException("stub key")

    mod.SSHException = SSHException
    mod.RejectPolicy = RejectPolicy
    mod.SSHClient = SSHClient
    mod.Ed25519Key = _KeyBase
    mod.RSAKey = _KeyBase
    mod.ECDSAKey = _KeyBase
    sys.modules["paramiko"] = mod


ensure_paramiko()
