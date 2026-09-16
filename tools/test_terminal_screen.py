"""真实 VT 解析的宽字符边界和终端状态回复回归。"""

import unittest

import pyte

from terminal_smoke import ReplyingScreen


class TerminalScreenTests(unittest.TestCase):
    def test_ascii_overwriting_wide_character_keeps_empty_continuation_cell(self):
        screen = ReplyingScreen(10, 2, lambda data: None)
        stream = pyte.Stream(screen)
        stream.feed('中文\rA')
        self.assertEqual(screen.display[0], 'A 文' + ' ' * 6)
        self.assertEqual(screen.buffer[0][1].data, '')
        self.assertEqual(screen.orphan_cell_fallbacks, 1)

    def test_device_status_uses_actual_parsed_cursor_and_does_not_draw_fake_text(self):
        replies = []
        screen = ReplyingScreen(10, 2, replies.append)
        stream = pyte.Stream(screen)
        stream.feed('中A\x1b[6n')
        self.assertEqual(replies, ['\x1b[1;4R'])
        self.assertEqual(screen.display[0], '中A' + ' ' * 7)


if __name__ == '__main__':
    unittest.main()
