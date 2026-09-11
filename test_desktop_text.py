import threading
import unittest
from contextlib import contextmanager
from unittest.mock import Mock

from ditado_desktop import DesktopTextAccess, FocusTarget, SelectionTooLongError


class DesktopTextTests(unittest.TestCase):
    def setUp(self):
        self.desktop = object.__new__(DesktopTextAccess)
        self.target = FocusTarget(10, 20, (1, 2), True)
        self.desktop.same_window = Mock(return_value=True)
        self.desktop.basic_focus = Mock(return_value=self.target)
        self.desktop.native_edit = Mock(return_value=None)
        self.desktop._editable = Mock(return_value=False)
        self.client, self.module, self.element = Mock(), Mock(), Mock()
        self.element.CurrentIsPassword = False
        self.element.GetRuntimeId.return_value = (1, 2)
        self.desktop._focused_element = Mock(return_value=self.element)
        self.client.ControlViewWalker.GetParentElement.return_value = None

        @contextmanager
        def automation():
            yield self.client, self.module
        self.desktop.automation = automation

    def test_missing_com_pattern_is_unavailable_not_a_selection_length_error(self):
        self.element.GetCurrentPattern.return_value.QueryInterface.side_effect = ValueError('NULL COM pointer access')
        text, _ = self.desktop.selected_text(self.target, threading.Event())
        self.assertEqual('', text)

    def test_reads_only_selected_ranges_not_whole_document(self):
        pattern = self.element.GetCurrentPattern.return_value.QueryInterface.return_value
        ranges = pattern.GetSelection.return_value
        ranges.Length = 1
        ranges.GetElement.return_value.GetText.return_value = 'selected paragraph'
        text, snapshot = self.desktop.selected_text(self.target, threading.Event())
        self.assertEqual('selected paragraph', text)
        self.assertFalse(snapshot.editable)
        pattern.DocumentRange.GetText.assert_not_called()

    def test_oversized_ranges_rejected(self):
        ranges = self.element.GetCurrentPattern.return_value.QueryInterface.return_value.GetSelection.return_value
        ranges.Length = 1
        ranges.GetElement.return_value.GetText.return_value = 'x' * 32001
        with self.assertRaises(SelectionTooLongError):
            self.desktop.selected_text(self.target, threading.Event())

    def test_password_selection_not_read(self):
        self.element.CurrentIsPassword = True
        self.assertEqual('', self.desktop.selected_text(self.target, threading.Event())[0])
        self.element.GetCurrentPattern.assert_not_called()

    def test_focus_changed_before_read_preserves_clipboard_and_skips_provider(self):
        self.desktop.same_window.return_value = False
        self.assertEqual('', self.desktop.selected_text(self.target, threading.Event())[0])
        self.desktop._focused_element.assert_not_called()

    def test_new_dom_field_in_same_window_is_not_paste_target(self):
        self.desktop.describe_focus = Mock(return_value=FocusTarget(10, 20, (1, 3), True))
        self.assertFalse(self.desktop.can_paste(self.target))

    def test_readonly_field_is_not_paste_target(self):
        self.desktop.describe_focus = Mock(return_value=FocusTarget(10, 20, (1, 2), False))
        self.assertFalse(self.desktop.can_paste(self.target))


if __name__ == '__main__':
    unittest.main()
