"""为真实终端回归提供 POSIX PTY 与 Windows ConPTY 子进程驱动。

本模块只传输终端输入输出，不解析或伪造屏幕内容；Windows 明确要求
pywinpty 的 ConPTY backend，不回退到旧 WinPTY。
"""

from __future__ import annotations

import os
from pathlib import Path
import select
import sys
import time


class TerminalSession:
    """启动并控制一个由本对象创建的交互式子进程。"""

    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str], rows: int, cols: int):
        if not argv:
            raise ValueError("argv must not be empty")
        if rows <= 0 or cols <= 0:
            raise ValueError("rows and cols must be positive")
        self._closed = False
        self._eof = False
        self._exit_code = None
        self.rows = rows
        self.cols = cols
        self._child = None
        self._backend = ""
        child_env = {str(key): str(value) for key, value in env.items()}
        if sys.platform == "win32":
            self._backend = "conpty"
            try:
                from winpty import Backend, PtyProcess
            except ImportError as error:
                raise RuntimeError("Windows terminal tests require pywinpty") from error
            try:
                backend = Backend.ConPTY
            except AttributeError as error:
                raise RuntimeError("installed pywinpty has no ConPTY backend") from error
            self._child = PtyProcess.spawn(
                list(argv), cwd=str(cwd), env=child_env,
                dimensions=(rows, cols), backend=backend,
            )
        else:
            self._backend = "posix-pty"
            try:
                import pexpect
            except ImportError as error:
                raise RuntimeError("POSIX terminal tests require pexpect") from error
            self._child = pexpect.spawn(
                argv[0], argv[1:], cwd=str(cwd), env=child_env,
                encoding="utf-8", dimensions=(rows, cols), echo=False,
            )

    @property
    def backend(self) -> str:
        return self._backend

    def read(self, timeout: float = 0.1) -> str:
        """读取当前可用输出；超时或 EOF 均返回空字符串。"""
        if self._closed or self._eof:
            return ""
        timeout = max(0.0, float(timeout))
        try:
            if self._backend == "posix-pty":
                import pexpect
                try:
                    value = self._child.read_nonblocking(size=4096, timeout=timeout)
                except (pexpect.TIMEOUT, TimeoutError):
                    return ""
                except pexpect.EOF:
                    self._eof = True
                    self._refresh_exit_code()
                    return ""
            else:
                # pywinpty's high-level read() is blocking. Its documented
                # implementation exposes the socket used by its reader thread;
                # select bounds the wait without inventing terminal replies.
                stream = getattr(self._child, "fileobj", None)
                if stream is None:
                    raise RuntimeError("pywinpty child has no readable stream")
                readable, _, _ = select.select([stream], [], [], timeout)
                if not readable:
                    return ""
                stream.settimeout(max(timeout, 0.001))
                try:
                    value = self._child.read(4096)
                except (TimeoutError, OSError):
                    return ""
        except (OSError, EOFError):
            self._eof = True
            self._refresh_exit_code()
            return ""
        if not value:
            self._refresh_exit_code()
        return value

    def write(self, text: str):
        if self._closed or self._eof:
            raise EOFError("terminal child is closed")
        if not isinstance(text, str):
            raise TypeError("text must be str")
        self._child.write(text)

    def resize(self, rows: int, cols: int):
        if rows <= 0 or cols <= 0:
            raise ValueError("rows and cols must be positive")
        if self._closed:
            return
        self._child.setwinsize(rows, cols)
        self.rows, self.cols = rows, cols

    def _refresh_exit_code(self):
        if self._exit_code is not None:
            return
        try:
            value = self._child.exitstatus
        except (AttributeError, OSError, EOFError):
            value = None
        if value is not None:
            self._exit_code = int(value)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            alive = bool(self._child.isalive())
        except (AttributeError, OSError, EOFError):
            alive = False
        if not alive:
            self._refresh_exit_code()
        return alive

    def exit_code(self):
        self._refresh_exit_code()
        return self._exit_code

    def close(self):
        """仅终止并释放本 session 创建的 child。可重复调用。"""
        if self._closed:
            return
        try:
            if self._backend == "posix-pty":
                self._child.close(force=True)
            else:
                self._child.close(force=True)
        finally:
            self._refresh_exit_code()
            self._closed = True
            self._eof = True

