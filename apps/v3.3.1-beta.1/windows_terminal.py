"""Windows ConPTY 的薄 VT 后端；按显示单元正确绘制 CJK，不依赖 PDCurses。"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
import threading
import unicodedata


class error(Exception):
    """与 curses.error 兼容的终端操作错误。"""


A_NORMAL = 0
A_BOLD = 1 << 0
A_DIM = 1 << 1
A_REVERSE = 1 << 2
_COLOR_SHIFT = 8
_COLOR_MASK = 0xFF << _COLOR_SHIFT

COLOR_BLACK = 0
COLOR_RED = 1
COLOR_GREEN = 2
COLOR_YELLOW = 3
COLOR_BLUE = 4
COLOR_MAGENTA = 5
COLOR_CYAN = 6
COLOR_WHITE = 7

KEY_DOWN = 258
KEY_UP = 259
KEY_LEFT = 260
KEY_RIGHT = 261
KEY_HOME = 262
KEY_BACKSPACE = 263
KEY_F0 = 264
KEY_F1 = KEY_F0 + 1
KEY_F2 = KEY_F0 + 2
KEY_F3 = KEY_F0 + 3
KEY_F4 = KEY_F0 + 4
KEY_F5 = KEY_F0 + 5
KEY_F6 = KEY_F0 + 6
KEY_F7 = KEY_F0 + 7
KEY_F8 = KEY_F0 + 8
KEY_F9 = KEY_F0 + 9
KEY_F10 = KEY_F0 + 10
KEY_F11 = KEY_F0 + 11
KEY_F12 = KEY_F0 + 12
KEY_DC = 330
KEY_NPAGE = 338
KEY_PPAGE = 339
KEY_ENTER = 343
KEY_END = 360
KEY_RESIZE = 410

_COLOR_TO_ANSI = {
    COLOR_BLACK: 30,
    COLOR_RED: 31,
    COLOR_GREEN: 32,
    COLOR_YELLOW: 33,
    COLOR_BLUE: 34,
    COLOR_MAGENTA: 35,
    COLOR_CYAN: 36,
    COLOR_WHITE: 37,
}
_color_pairs = {}
_escdelay = 30
_cursor_visibility = 1
_active_screen = None
_active_transport = None
_active_lock = threading.RLock()
_NEED_MORE_UTF16 = object()


def _cell_width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def _printable_char(char):
    return " " if unicodedata.category(char).startswith("C") else char


def raw():
    return None


def noecho():
    return None


def set_escdelay(value):
    global _escdelay
    if type(value) is not int or value < 0:
        raise error("ESC delay must be a non-negative integer")
    _escdelay = value


def curs_set(visibility):
    global _cursor_visibility
    if visibility not in (0, 1, 2):
        raise error("cursor visibility must be 0, 1, or 2")
    previous = _cursor_visibility
    _cursor_visibility = visibility
    if _active_screen is not None and visibility != previous:
        _active_screen._set_cursor_visibility(visibility)
    return previous


def has_colors():
    return True


def start_color():
    return None


def use_default_colors():
    return None


def init_pair(index, foreground, background):
    if type(index) is not int or not 1 <= index <= 255:
        raise error("color pair index is out of range")
    if foreground not in _COLOR_TO_ANSI or background not in (-1,) + tuple(_COLOR_TO_ANSI):
        raise error("unsupported color")
    _color_pairs[index] = (foreground, background)


def color_pair(index):
    if type(index) is not int or not 0 <= index <= 255:
        raise error("color pair index is out of range")
    return index << _COLOR_SHIFT


@dataclass
class _Cell:
    text: str = " "
    style: int = 0
    continuation: bool = False
    owner: int = -1
    span: int = 1


class Screen:
    """ielts.py 所需的单窗口 curses 子集。"""

    def __init__(self, transport):
        self._transport = transport
        self._rows, self._cols = self._validated_size(transport.size())
        self._grid = self._blank_grid(self._rows, self._cols)
        self._flushed = [None] * self._rows
        self._cursor_row = 0
        self._cursor_col = 0
        self._last_cursor = None
        self._last_visibility = None
        self._timeout_ms = -1
        self._keypad = False

    @staticmethod
    def _validated_size(value):
        try:
            rows, cols = value
        except (TypeError, ValueError) as exc:
            raise error("invalid console size") from exc
        if type(rows) is not int or type(cols) is not int or rows < 1 or cols < 1:
            raise error("invalid console size")
        return rows, cols

    @staticmethod
    def _blank_grid(rows, cols):
        return [[_Cell() for _ in range(cols)] for _ in range(rows)]

    def _resize(self, rows, cols):
        if (rows, cols) == (self._rows, self._cols):
            return False
        self._rows, self._cols = rows, cols
        self._grid = self._blank_grid(rows, cols)
        self._flushed = [None] * rows
        self._cursor_row = min(self._cursor_row, rows - 1)
        self._cursor_col = min(self._cursor_col, cols - 1)
        self._last_cursor = None
        return True

    def _check_resize(self):
        rows, cols = self._validated_size(self._transport.size())
        return self._resize(rows, cols)

    def getmaxyx(self):
        self._check_resize()
        return self._rows, self._cols

    def getyx(self):
        return self._cursor_row, self._cursor_col

    def erase(self):
        self._grid = self._blank_grid(self._rows, self._cols)
        self._cursor_row = 0
        self._cursor_col = 0

    def keypad(self, enabled):
        self._keypad = bool(enabled)

    def timeout(self, milliseconds):
        if type(milliseconds) is not int:
            raise error("timeout must be an integer")
        self._timeout_ms = milliseconds

    def move(self, row, col):
        if type(row) is not int or type(col) is not int or not (0 <= row < self._rows and 0 <= col < self._cols):
            raise error("cursor position is outside the screen")
        self._cursor_row = row
        self._cursor_col = col

    def _clear_occupant(self, row, col):
        cell = self._grid[row][col]
        owner = cell.owner if cell.continuation else col
        base = self._grid[row][owner]
        span = base.span if not base.continuation else 1
        for position in range(owner, min(self._cols, owner + max(1, span))):
            self._grid[row][position] = _Cell()

    def _put_base(self, row, col, text, style, span):
        for position in range(col, col + span):
            self._clear_occupant(row, position)
        self._grid[row][col] = _Cell(text=text, style=style, span=span)
        for position in range(col + 1, col + span):
            self._grid[row][position] = _Cell(text="", style=style, continuation=True, owner=col, span=0)

    def addstr(self, row, col, text, style=0):
        if type(row) is not int or type(col) is not int or not isinstance(text, str) or type(style) is not int:
            raise error("invalid addstr arguments")
        if not (0 <= row < self._rows and 0 <= col < self._cols):
            raise error("text position is outside the screen")
        cursor = col
        for original in text:
            char = _printable_char(original)
            width = _cell_width(char)
            if width == 0:
                if cursor > 0:
                    owner = self._grid[row][cursor - 1].owner if self._grid[row][cursor - 1].continuation else cursor - 1
                    self._grid[row][owner].text += char
                continue
            if cursor + width > self._cols:
                break
            self._put_base(row, cursor, char, style, width)
            cursor += width
        self._cursor_row = row
        self._cursor_col = min(cursor, self._cols - 1)

    @staticmethod
    def _style_sequence(style):
        codes = []
        if style & A_BOLD:
            codes.append("1")
        if style & A_DIM:
            codes.append("2")
        if style & A_REVERSE:
            codes.append("7")
        pair = (style & _COLOR_MASK) >> _COLOR_SHIFT
        if pair:
            foreground, background = _color_pairs.get(pair, (-1, -1))
            if foreground in _COLOR_TO_ANSI:
                codes.append(str(_COLOR_TO_ANSI[foreground]))
            if background in _COLOR_TO_ANSI:
                codes.append(str(_COLOR_TO_ANSI[background] + 10))
        return "\x1b[" + (";".join(codes) if codes else "0") + "m"

    def _row_signature(self, row):
        return tuple((cell.text, cell.style, cell.continuation, cell.owner, cell.span) for cell in row)

    def _render_row(self, row):
        last = -1
        for index, cell in enumerate(row):
            if not cell.continuation and cell.text != " ":
                last = max(last, index + cell.span - 1)
        if last < 0:
            return ""
        pieces = []
        current_style = None
        for index, cell in enumerate(row[:last + 1]):
            if cell.continuation:
                continue
            if cell.style != current_style:
                pieces.append(self._style_sequence(cell.style))
                current_style = cell.style
            pieces.append(cell.text)
        pieces.append("\x1b[0m")
        return "".join(pieces)

    def _set_cursor_visibility(self, visibility):
        self._last_visibility = None

    def refresh(self):
        chunks = []
        for row_index, row in enumerate(self._grid):
            signature = self._row_signature(row)
            if signature != self._flushed[row_index]:
                chunks.append("\x1b[%d;1H\x1b[2K" % (row_index + 1))
                chunks.append(self._render_row(row))
                self._flushed[row_index] = signature
        visibility = _cursor_visibility
        if visibility != self._last_visibility:
            chunks.append("\x1b[?25h" if visibility else "\x1b[?25l")
            self._last_visibility = visibility
        cursor = (self._cursor_row, self._cursor_col)
        if chunks or cursor != self._last_cursor:
            chunks.append("\x1b[%d;%dH" % (cursor[0] + 1, cursor[1] + 1))
            self._last_cursor = cursor
        if chunks:
            self._transport.write("".join(chunks))

    def get_wch(self):
        if self._check_resize():
            return KEY_RESIZE
        # ielts.py always requests 100 ms. Keep negative values bounded so exit
        # and signal handling never depend on an unbounded WinAPI wait.
        wait_ms = self._timeout_ms if self._timeout_ms >= 0 else 250
        value = self._transport.read_key(wait_ms)
        if value is None:
            raise error("no input")
        if value == KEY_RESIZE:
            self._check_resize()
        return value


class _COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class _SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]


class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", _COORD), ("dwCursorPosition", _COORD),
                ("wAttributes", wintypes.WORD), ("srWindow", _SMALL_RECT),
                ("dwMaximumWindowSize", _COORD)]


class _CONSOLE_CURSOR_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("bVisible", wintypes.BOOL)]


class _CHAR_UNION(ctypes.Union):
    _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", wintypes.CHAR)]


class _KEY_EVENT_RECORD(ctypes.Structure):
    _anonymous_ = ("uChar",)
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                ("uChar", _CHAR_UNION), ("dwControlKeyState", wintypes.DWORD)]


class _EVENT_UNION(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT_RECORD), ("WindowBufferSizeEvent", _COORD),
                ("padding", ctypes.c_byte * 16)]


class _INPUT_RECORD(ctypes.Structure):
    _anonymous_ = ("Event",)
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT_UNION)]


class _WindowsConsoleTransport:
    """在 wrapper 生命周期内拥有控制台 mode、code page 与备用屏幕。"""

    STD_INPUT_HANDLE = -10
    STD_OUTPUT_HANDLE = -11
    ENABLE_PROCESSED_INPUT = 0x0001
    ENABLE_LINE_INPUT = 0x0002
    ENABLE_ECHO_INPUT = 0x0004
    ENABLE_WINDOW_INPUT = 0x0008
    ENABLE_QUICK_EDIT_MODE = 0x0040
    ENABLE_EXTENDED_FLAGS = 0x0080
    ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
    ENABLE_PROCESSED_OUTPUT = 0x0001
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    DISABLE_NEWLINE_AUTO_RETURN = 0x0008
    KEY_EVENT = 0x0001
    WINDOW_BUFFER_SIZE_EVENT = 0x0004
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258

    _VK_MAP = {
        0x08: KEY_BACKSPACE,
        0x0D: KEY_ENTER,
        0x21: KEY_PPAGE,
        0x22: KEY_NPAGE,
        0x23: KEY_END,
        0x24: KEY_HOME,
        0x25: KEY_LEFT,
        0x26: KEY_UP,
        0x27: KEY_RIGHT,
        0x28: KEY_DOWN,
        0x2E: KEY_DC,
    }
    _VK_MAP.update({0x70 + index: KEY_F0 + index + 1 for index in range(12)})

    def __init__(self):
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            raise error("Windows VT backend requires a Windows console")
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()
        self._input = self._kernel.GetStdHandle(self.STD_INPUT_HANDLE)
        self._output = self._kernel.GetStdHandle(self.STD_OUTPUT_HANDLE)
        invalid = ctypes.c_void_p(-1).value
        if self._input in (None, invalid) or self._output in (None, invalid):
            raise error("standard input/output is not a Windows console")
        self._saved = None
        self._entered = False
        self._repeat = []
        self._pending_high_surrogate = None

    def _bind(self):
        kernel = self._kernel
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetConsoleMode.restype = wintypes.BOOL
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetConsoleMode.restype = wintypes.BOOL
        kernel.GetConsoleCP.restype = wintypes.UINT
        kernel.GetConsoleOutputCP.restype = wintypes.UINT
        kernel.SetConsoleCP.argtypes = [wintypes.UINT]
        kernel.SetConsoleOutputCP.argtypes = [wintypes.UINT]
        kernel.SetConsoleCP.restype = wintypes.BOOL
        kernel.SetConsoleOutputCP.restype = wintypes.BOOL
        kernel.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_CONSOLE_SCREEN_BUFFER_INFO)]
        kernel.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
        kernel.GetConsoleCursorInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_CONSOLE_CURSOR_INFO)]
        kernel.GetConsoleCursorInfo.restype = wintypes.BOOL
        kernel.SetConsoleCursorInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_CONSOLE_CURSOR_INFO)]
        kernel.SetConsoleCursorInfo.restype = wintypes.BOOL
        kernel.WriteConsoleW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        kernel.WriteConsoleW.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReadConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_INPUT_RECORD),
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.ReadConsoleInputW.restype = wintypes.BOOL

    @staticmethod
    def _raise_last(message):
        value = ctypes.get_last_error()
        raise error("%s (WinError %d)" % (message, value))

    def _get_mode(self, handle):
        value = wintypes.DWORD()
        if not self._kernel.GetConsoleMode(handle, ctypes.byref(value)):
            self._raise_last("GetConsoleMode failed")
        return value.value

    def enter(self):
        input_mode = self._get_mode(self._input)
        output_mode = self._get_mode(self._output)
        cursor = _CONSOLE_CURSOR_INFO()
        if not self._kernel.GetConsoleCursorInfo(self._output, ctypes.byref(cursor)):
            self._raise_last("GetConsoleCursorInfo failed")
        self._saved = {
            "input_mode": input_mode,
            "output_mode": output_mode,
            "input_cp": self._kernel.GetConsoleCP(),
            "output_cp": self._kernel.GetConsoleOutputCP(),
            "cursor_size": cursor.dwSize,
            "cursor_visible": bool(cursor.bVisible),
        }
        try:
            if not self._kernel.SetConsoleCP(65001) or not self._kernel.SetConsoleOutputCP(65001):
                self._raise_last("setting UTF-8 console code page failed")
            new_input = self._raw_input_mode(input_mode)
            if not self._kernel.SetConsoleMode(self._input, new_input):
                self._raise_last("setting raw console input failed")
            new_output = output_mode | self.ENABLE_PROCESSED_OUTPUT | self.ENABLE_VIRTUAL_TERMINAL_PROCESSING
            new_output |= self.DISABLE_NEWLINE_AUTO_RETURN
            if not self._kernel.SetConsoleMode(self._output, new_output):
                self._raise_last("enabling virtual terminal output failed")
            self._entered = True
            self.write("\x1b[?1049h\x1b[2J\x1b[H")
        except Exception:
            self._restore()
            raise

    @classmethod
    def _raw_input_mode(cls, input_mode):
        value = input_mode
        value &= ~(cls.ENABLE_PROCESSED_INPUT | cls.ENABLE_LINE_INPUT |
                   cls.ENABLE_ECHO_INPUT | cls.ENABLE_QUICK_EDIT_MODE |
                   cls.ENABLE_VIRTUAL_TERMINAL_INPUT)
        value |= cls.ENABLE_WINDOW_INPUT | cls.ENABLE_EXTENDED_FLAGS
        return value

    def _restore(self):
        saved = self._saved
        if saved is None:
            return
        if self._entered:
            try:
                self.write("\x1b[0m\x1b[?25h\x1b[?1049l")
            except Exception:
                pass
        self._entered = False
        try:
            self._kernel.SetConsoleMode(self._output, saved["output_mode"])
            self._kernel.SetConsoleMode(self._input, saved["input_mode"])
            self._kernel.SetConsoleOutputCP(saved["output_cp"])
            self._kernel.SetConsoleCP(saved["input_cp"])
            cursor = _CONSOLE_CURSOR_INFO(saved["cursor_size"], saved["cursor_visible"])
            self._kernel.SetConsoleCursorInfo(self._output, ctypes.byref(cursor))
        finally:
            self._saved = None

    def exit(self):
        self._restore()

    def size(self):
        info = _CONSOLE_SCREEN_BUFFER_INFO()
        if not self._kernel.GetConsoleScreenBufferInfo(self._output, ctypes.byref(info)):
            self._raise_last("GetConsoleScreenBufferInfo failed")
        return info.srWindow.Bottom - info.srWindow.Top + 1, info.srWindow.Right - info.srWindow.Left + 1

    def write(self, value):
        if not value:
            return
        units = len(value.encode("utf-16-le")) // 2
        written = wintypes.DWORD()
        if not self._kernel.WriteConsoleW(self._output, value, units, ctypes.byref(written), None):
            self._raise_last("WriteConsoleW failed")
        if written.value != units:
            raise error("WriteConsoleW wrote an incomplete frame")

    def read_key(self, timeout_ms):
        if self._repeat:
            return self._repeat.pop(0)
        deadline = max(0, timeout_ms)
        while True:
            result = self._kernel.WaitForSingleObject(self._input, deadline)
            if result == self.WAIT_TIMEOUT:
                return self._finish_pending_surrogate()
            if result != self.WAIT_OBJECT_0:
                self._raise_last("waiting for console input failed")
            record = _INPUT_RECORD()
            count = wintypes.DWORD()
            if not self._kernel.ReadConsoleInputW(self._input, ctypes.byref(record), 1, ctypes.byref(count)):
                self._raise_last("ReadConsoleInputW failed")
            if not count.value:
                return None
            if record.EventType == self.WINDOW_BUFFER_SIZE_EVENT:
                return KEY_RESIZE
            if record.EventType != self.KEY_EVENT or not record.KeyEvent.bKeyDown:
                deadline = 0
                continue
            key = self._decode_key(record.KeyEvent)
            if key is None:
                deadline = 0
                continue
            repeats = max(1, record.KeyEvent.wRepeatCount)
            key = self._normalize_utf16_key(key, repeats)
            if key is _NEED_MORE_UTF16:
                deadline = 0
                continue
            return key

    def _finish_pending_surrogate(self):
        if self._pending_high_surrogate is None:
            return None
        self._pending_high_surrogate = None
        return "\ufffd"

    def _normalize_utf16_key(self, key, repeats=1):
        if not isinstance(key, str) or len(key) != 1:
            if self._pending_high_surrogate is not None:
                self._pending_high_surrogate = None
                self._repeat[0:0] = [key] * repeats
                return "\ufffd"
            if repeats > 1:
                self._repeat.extend([key] * (repeats - 1))
            return key
        codepoint = ord(key)
        if self._pending_high_surrogate is not None:
            high = ord(self._pending_high_surrogate)
            self._pending_high_surrogate = None
            if 0xDC00 <= codepoint <= 0xDFFF:
                combined = chr(0x10000 + ((high - 0xD800) << 10) + codepoint - 0xDC00)
                if repeats > 1:
                    self._repeat.extend([combined] * (repeats - 1))
                return combined
            if 0xD800 <= codepoint <= 0xDBFF:
                self._pending_high_surrogate = key
                return "\ufffd"
            self._repeat[0:0] = [key] * repeats
            return "\ufffd"
        if 0xD800 <= codepoint <= 0xDBFF:
            self._pending_high_surrogate = key
            return _NEED_MORE_UTF16
        if 0xDC00 <= codepoint <= 0xDFFF:
            key = "\ufffd"
        if repeats > 1:
            self._repeat.extend([key] * (repeats - 1))
        return key

    @classmethod
    def _decode_key(cls, event):
        virtual = event.wVirtualKeyCode
        if virtual in cls._VK_MAP:
            return cls._VK_MAP[virtual]
        if virtual == 0x1B:
            return "\x1b"
        char = event.UnicodeChar
        if char == "\r":
            return KEY_ENTER
        if char == "\b":
            return KEY_BACKSPACE
        if char and char != "\x00":
            return char
        return None


def wrapper(callback, *args, **kwargs):
    """建立隔离的 Windows VT 会话，并保证异常路径恢复控制台。"""
    global _active_screen, _active_transport, _cursor_visibility
    transport = kwargs.pop("_transport", None) or _WindowsConsoleTransport()
    with _active_lock:
        if _active_transport is not None:
            raise error("nested terminal wrappers are not supported")
        entered = False
        try:
            transport.enter()
            entered = True
            _cursor_visibility = 1
            _active_transport = transport
            _active_screen = Screen(transport)
            return callback(_active_screen, *args, **kwargs)
        finally:
            _active_screen = None
            _active_transport = None
            if entered:
                transport.exit()
