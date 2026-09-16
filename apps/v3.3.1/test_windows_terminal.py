#!/usr/bin/env python3
"""Windows VT 薄后端的跨平台网格、输入映射与恢复契约测试。"""

import unittest

import windows_terminal as terminal


class FakeTransport:
    def __init__(self, rows=6, cols=40, events=None, lifecycle=None):
        self.rows = rows
        self.cols = cols
        self.events = list(events or [])
        self.writes = []
        self.waits = []
        self.lifecycle = lifecycle if lifecycle is not None else []

    def enter(self):
        self.lifecycle.extend(["save", "set-codepage", "set-modes", "alternate-on"])

    def exit(self):
        self.lifecycle.extend(["alternate-off", "restore-modes", "restore-codepage"])

    def size(self):
        return self.rows, self.cols

    def write(self, value):
        self.writes.append(value)

    def read_key(self, timeout_ms):
        self.waits.append(timeout_ms)
        return self.events.pop(0) if self.events else None


class WindowsTerminalTests(unittest.TestCase):
    def setUp(self):
        terminal._color_pairs.clear()
        terminal._cursor_visibility = 1

    def test_cjk_grid_uses_two_cells_but_outputs_each_character_once(self):
        transport = FakeTransport()
        screen = terminal.Screen(transport)
        screen.addstr(0, 0, "A今日B")
        self.assertEqual(screen.getyx(), (0, 6))
        self.assertTrue(screen._grid[0][2].continuation)
        self.assertEqual(screen._grid[0][2].owner, 1)
        self.assertTrue(screen._grid[0][4].continuation)
        screen.refresh()
        frame = transport.writes[-1]
        self.assertIn("A今日B", frame)
        self.assertNotIn("今 今", frame)
        self.assertIn("\x1b[1;1H\x1b[2K", frame)

    def test_overwriting_half_of_wide_character_clears_the_whole_character(self):
        transport = FakeTransport()
        screen = terminal.Screen(transport)
        screen.addstr(0, 0, "A今日B")
        screen.refresh()
        screen.addstr(0, 2, "X")
        screen.refresh()
        frame = transport.writes[-1]
        self.assertIn("A X日B", frame)
        self.assertNotIn("今", frame)
        self.assertFalse(screen._grid[0][1].continuation)
        self.assertEqual(screen._grid[0][1].text, " ")

    def test_combining_mark_stays_with_base_without_advancing_column(self):
        screen = terminal.Screen(FakeTransport())
        screen.addstr(0, 0, "e\u0301中")
        self.assertEqual(screen.getyx(), (0, 3))
        self.assertEqual(screen._grid[0][0].text, "e\u0301")
        self.assertTrue(screen._grid[0][2].continuation)

    def test_control_characters_cannot_inject_terminal_sequences(self):
        transport = FakeTransport()
        screen = terminal.Screen(transport)
        screen.addstr(0, 0, "safe\x1b[31m")
        screen.refresh()
        frame = transport.writes[-1]
        self.assertIn("safe [31m", frame)
        self.assertNotIn("safe\x1b[31m", frame)

    def test_refresh_rewrites_only_rows_whose_final_cells_changed(self):
        transport = FakeTransport(rows=3, cols=20)
        screen = terminal.Screen(transport)
        screen.erase()
        screen.addstr(0, 0, "标题")
        screen.addstr(1, 0, "body")
        screen.refresh()
        first_writes = len(transport.writes)
        screen.erase()
        screen.addstr(0, 0, "标题")
        screen.addstr(1, 0, "body")
        screen.refresh()
        self.assertEqual(len(transport.writes), first_writes)
        screen.erase()
        screen.addstr(0, 0, "标题")
        screen.addstr(1, 0, "changed")
        screen.refresh()
        frame = transport.writes[-1]
        self.assertNotIn("\x1b[1;1H\x1b[2K", frame)
        self.assertIn("\x1b[2;1H\x1b[2K", frame)

    def test_styles_and_color_pairs_generate_vt_sgr(self):
        terminal.init_pair(1, terminal.COLOR_CYAN, -1)
        transport = FakeTransport()
        screen = terminal.Screen(transport)
        screen.addstr(0, 0, "word", terminal.color_pair(1) | terminal.A_BOLD)
        screen.addstr(1, 0, "selected", terminal.A_REVERSE)
        screen.refresh()
        frame = transport.writes[-1]
        self.assertIn("\x1b[1;36mword", frame)
        self.assertIn("\x1b[7mselected", frame)

    def test_resize_is_reported_before_input_and_rebuilds_grid(self):
        transport = FakeTransport(rows=6, cols=40, events=["x"])
        screen = terminal.Screen(transport)
        screen.timeout(100)
        transport.rows, transport.cols = 8, 52
        self.assertEqual(screen.get_wch(), terminal.KEY_RESIZE)
        self.assertEqual(screen.getmaxyx(), (8, 52))
        self.assertEqual(screen.get_wch(), "x")
        self.assertEqual(transport.waits, [100])

    def test_timeout_raises_compatible_error_and_never_requests_infinite_wait(self):
        transport = FakeTransport()
        screen = terminal.Screen(transport)
        screen.timeout(-1)
        with self.assertRaises(terminal.error):
            screen.get_wch()
        self.assertEqual(transport.waits, [250])

    def test_key_event_mapping_covers_function_navigation_and_text(self):
        def event(virtual, char="\x00"):
            value = terminal._KEY_EVENT_RECORD()
            value.bKeyDown = True
            value.wRepeatCount = 1
            value.wVirtualKeyCode = virtual
            value.UnicodeChar = char
            return value

        self.assertEqual(terminal._WindowsConsoleTransport._decode_key(event(0x7A)), terminal.KEY_F11)
        self.assertEqual(terminal._WindowsConsoleTransport._decode_key(event(0x78)), terminal.KEY_F9)
        self.assertEqual(terminal._WindowsConsoleTransport._decode_key(event(0x26)), terminal.KEY_UP)
        self.assertEqual(terminal._WindowsConsoleTransport._decode_key(event(0x2E)), terminal.KEY_DC)
        self.assertEqual(terminal._WindowsConsoleTransport._decode_key(event(0x41, "a")), "a")
        self.assertEqual(terminal._WindowsConsoleTransport._decode_key(event(0x51, "\x11")), "\x11")

    def test_raw_input_mode_disables_vt_sequences_and_enables_window_events(self):
        backend = terminal._WindowsConsoleTransport
        original = (backend.ENABLE_PROCESSED_INPUT | backend.ENABLE_LINE_INPUT |
                    backend.ENABLE_ECHO_INPUT | backend.ENABLE_QUICK_EDIT_MODE |
                    backend.ENABLE_VIRTUAL_TERMINAL_INPUT | 0x0010)
        value = backend._raw_input_mode(original)
        cleared = (backend.ENABLE_PROCESSED_INPUT | backend.ENABLE_LINE_INPUT |
                   backend.ENABLE_ECHO_INPUT | backend.ENABLE_QUICK_EDIT_MODE |
                   backend.ENABLE_VIRTUAL_TERMINAL_INPUT)
        self.assertEqual(value & cleared, 0)
        self.assertTrue(value & backend.ENABLE_WINDOW_INPUT)
        self.assertTrue(value & backend.ENABLE_EXTENDED_FLAGS)
        self.assertTrue(value & 0x0010)

    def test_utf16_surrogate_pairs_are_combined_and_orphans_are_replaced(self):
        backend = object.__new__(terminal._WindowsConsoleTransport)
        backend._repeat = []
        backend._pending_high_surrogate = None
        self.assertIs(backend._normalize_utf16_key("\ud83d"), terminal._NEED_MORE_UTF16)
        self.assertEqual(backend._normalize_utf16_key("\ude00"), "😀")
        self.assertIsNone(backend._pending_high_surrogate)
        self.assertEqual(backend._normalize_utf16_key("\ude00"), "\ufffd")

        self.assertIs(backend._normalize_utf16_key("\ud83d"), terminal._NEED_MORE_UTF16)
        self.assertEqual(backend._normalize_utf16_key(terminal.KEY_F11), "\ufffd")
        self.assertEqual(backend._repeat.pop(0), terminal.KEY_F11)
        self.assertIsNone(backend._pending_high_surrogate)

        self.assertIs(backend._normalize_utf16_key("\ud83d"), terminal._NEED_MORE_UTF16)
        self.assertEqual(backend._finish_pending_surrogate(), "\ufffd")
        self.assertIsNone(backend._pending_high_surrogate)

    def test_repeated_curs_set_does_not_emit_duplicate_visibility_sequence(self):
        transport = FakeTransport()
        screen = terminal.Screen(transport)
        old_screen = terminal._active_screen
        try:
            terminal._active_screen = screen
            screen.refresh()
            count = len(transport.writes)
            terminal.curs_set(1)
            screen.refresh()
            self.assertEqual(len(transport.writes), count)
            terminal.curs_set(0)
            screen.refresh()
            self.assertIn("\x1b[?25l", transport.writes[-1])
        finally:
            terminal._active_screen = old_screen
            terminal._cursor_visibility = 1

    def test_wrapper_restores_transport_after_callback_error_in_reverse_order(self):
        lifecycle = []
        transport = FakeTransport(lifecycle=lifecycle)

        def fail(screen):
            lifecycle.append("callback")
            raise RuntimeError("stop")

        with self.assertRaisesRegex(RuntimeError, "stop"):
            terminal.wrapper(fail, _transport=transport)
        self.assertEqual(lifecycle, [
            "save", "set-codepage", "set-modes", "alternate-on", "callback",
            "alternate-off", "restore-modes", "restore-codepage",
        ])
        self.assertIsNone(terminal._active_screen)
        self.assertIsNone(terminal._active_transport)

    def test_wrapper_passes_screen_arguments_and_returns_callback_value(self):
        transport = FakeTransport()

        def callback(screen, number, suffix=None):
            self.assertIsInstance(screen, terminal.Screen)
            return str(number) + suffix

        self.assertEqual(terminal.wrapper(callback, 7, suffix="!", _transport=transport), "7!")

    def test_move_and_addstr_validate_bounds_without_partial_wide_glyph(self):
        screen = terminal.Screen(FakeTransport(rows=2, cols=4))
        with self.assertRaises(terminal.error):
            screen.move(2, 0)
        screen.addstr(0, 3, "中")
        self.assertEqual(screen._grid[0][3].text, " ")
        self.assertEqual(screen.getyx(), (0, 3))


if __name__ == "__main__":
    unittest.main()
