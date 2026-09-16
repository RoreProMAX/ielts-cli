"""Small operating-system compatibility layer used by the portable IELTS CLI.

The release sources use ``fcntl.flock`` and ``os.fchmod`` for process locks and
private state files.  This module keeps those call sites unchanged while
providing Windows equivalents.  It intentionally contains no user, machine, or
release-directory paths.
"""

import errno
import os
import signal


_IS_WINDOWS = os.name == "nt"


def private_file(fd):
    """Apply private-file permissions to an already-open file descriptor.

    POSIX uses an atomic descriptor operation, equivalent to the release
    sources' ``os.fchmod(fd, 0o600)``.  Windows has no portable chmod mode that
    removes inherited ACL access; the OS ACL is therefore left intact.  The
    existing directory ACL is retained on Windows.  This matters because
    ``--portable`` may intentionally point at any shared directory, so the
    adapter does not assume a private user-data location.  Returning ``None``
    matches ``os.fchmod``.
    """

    if not _IS_WINDOWS:
        return os.fchmod(fd, 0o600)
    return None


def shutdown_signals():
    """Return signals supported by the CLI's graceful-shutdown handlers."""

    if _IS_WINDOWS:
        # SIGBREAK is the Windows console Ctrl+Break signal.  Some Python
        # builds/platform shims do not expose it, so feature-detect it.
        signals = [signal.SIGTERM]
        sigbreak = getattr(signal, "SIGBREAK", None)
        if sigbreak is not None:
            signals.append(sigbreak)
        return tuple(signals)
    return (signal.SIGTERM, signal.SIGHUP)


if _IS_WINDOWS:
    import msvcrt

    class _WindowsFileLocks(object):
        """fcntl.flock-shaped adapter backed by a one-byte msvcrt lock."""

        LOCK_EX = 2
        LOCK_NB = 4
        LOCK_UN = 8
        _held_fds = set()

        @staticmethod
        def _ensure_lock_byte(fd):
            """Ensure the lock region exists without truncating the file."""

            current = os.lseek(fd, 0, os.SEEK_CUR)
            try:
                if os.fstat(fd).st_size < 1:
                    os.lseek(fd, 0, os.SEEK_END)
                    os.write(fd, b"\0")
            finally:
                os.lseek(fd, current, os.SEEK_SET)

        @classmethod
        def flock(cls, fd, operation):
            # Do not treat LOCK_UN|LOCK_NB or unknown combinations as an
            # acquire request.  Unlocking must target exactly the lock byte.
            if operation & cls.LOCK_UN:
                if operation & ~(cls.LOCK_UN):
                    raise ValueError("LOCK_UN cannot be combined with flags")
                # reminders.py unconditionally unlocks in a finally block,
                # including after a failed non-blocking acquire.  msvcrt
                # raises for an unlock the current process does not hold;
                # POSIX flock permits that call, so make the adapter match it.
                if fd not in cls._held_fds:
                    return None
                cls._ensure_lock_byte(fd)
                current = os.lseek(fd, 0, os.SEEK_CUR)
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                finally:
                    os.lseek(fd, current, os.SEEK_SET)
                    cls._held_fds.discard(fd)
                return None

            if operation & ~ (cls.LOCK_EX | cls.LOCK_NB):
                raise ValueError("unsupported flock operation")
            if not operation & cls.LOCK_EX:
                raise ValueError("only exclusive locks are supported")

            cls._ensure_lock_byte(fd)
            mode = msvcrt.LK_NBLCK if operation & cls.LOCK_NB else msvcrt.LK_LOCK
            current = os.lseek(fd, 0, os.SEEK_CUR)
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, mode, 1)
                except OSError as exc:
                    # Keep the release code's existing ``except
                    # BlockingIOError`` behavior on Windows.
                    if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise BlockingIOError(errno.EAGAIN, "lock unavailable")
                    raise
                cls._held_fds.add(fd)
            finally:
                os.lseek(fd, current, os.SEEK_SET)

    file_locks = _WindowsFileLocks
else:
    import fcntl as file_locks
