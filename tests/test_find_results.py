import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


class Region:
    def __init__(self, a, b):
        self.a, self.b = a, b


class TextCommand:
    def __init__(self, view):
        self.view = view


class WindowCommand:
    def __init__(self, window):
        self.window = window


sublime = types.SimpleNamespace(
    Region=Region, HIDDEN=0, DRAW_OUTLINED=1,
    windows=Mock(), error_message=Mock(), message_dialog=Mock(),
    status_message=Mock(), set_timeout=lambda callback, delay: callback(),
)
sublime_plugin = types.SimpleNamespace(
    TextCommand=TextCommand, WindowCommand=WindowCommand, EventListener=object,
)
spec = importlib.util.spec_from_file_location(
    "find_results_plugin", Path(__file__).resolve().parents[1] / "FindResultsApplyChanges.py"
)
plugin = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"sublime": sublime, "sublime_plugin": sublime_plugin}):
    spec.loader.exec_module(plugin)


class View:
    def __init__(self, text="", name="", filename=None, dirty=False):
        self.text, self._name, self._filename, self.dirty = text, name, filename, dirty
        self.options, self.regions, self.commands = {}, {}, []

    def name(self):
        return self._name

    def set_name(self, name):
        self._name = name

    def file_name(self):
        return self._filename

    def is_dirty(self):
        return self.dirty

    def settings(self):
        return self

    def get(self, key, default=None):
        return self.options.get(key, default)

    def set(self, key, value):
        self.options[key] = value

    def set_syntax_file(self, syntax):
        self.set("syntax", syntax)

    def set_scratch(self, scratch):
        self.scratch = scratch

    def size(self):
        return len(self.text)

    def substr(self, region):
        return self.text[region.a:region.b]

    def find_all(self, pattern):
        return [Region(match.start(), match.end()) for match in re.finditer(pattern, self.text, re.MULTILINE)]

    def erase_regions(self, key):
        self.regions.pop(key, None)

    def add_regions(self, key, regions, *args):
        self.regions[key] = regions

    def get_regions(self, key):
        return self.regions[key]

    def run_command(self, command, args=None):
        self.commands.append(command)
        if command == "append":
            self.text += args["characters"]
        elif command == "find_results_apply_changes":
            plugin.FindResultsApplyChangesCommand(self).run(None)


class Window:
    def __init__(self, directory):
        self.directory, self.current, self.open_views = directory, None, []
        self.show_input_panel = Mock()

    def active_view(self):
        return self.current

    def folders(self):
        return [str(self.directory)]

    def views(self):
        return self.open_views

    def new_file(self):
        self.current = View()
        self.open_views.append(self.current)
        return self.current


class FindResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.window = Window(self.root)
        for mock in (sublime.error_message, sublime.message_dialog, sublime.status_message):
            mock.reset_mock()
        sublime.windows.return_value = [self.window]

    def file(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents.encode("utf8"))
        return path

    def native(self, text):
        view = self.window.new_file()
        view.set_name("Find Results")
        view.text = text
        return view

    def apply(self, view):
        plugin.FindResultsApplyChangesCommand(view).run(None)

    def import_grep(self, text):
        source = self.window.new_file()
        source.text = text
        plugin.GrepResultsToFindResultsCommand(self.window).run(base_dir=str(self.root))
        return self.window.active_view()

    def test_native_expands_home_paths(self):
        path = self.file("native.txt", "before\nafter\n")
        view = self.native("\n~/native.txt:\n  1: changed\n\n1 match in 1 file\n")
        with patch.dict(os.environ, {"HOME": str(self.root), "USERPROFILE": str(self.root)}):
            self.apply(view)
        self.assertEqual(path.read_text(), "changed\nafter\n")
        sublime.error_message.assert_not_called()

    def test_native_multiple_files_context_and_whitespace(self):
        first = self.file("a.txt", "first\ncontext\n")
        last = self.file("z.txt", "last\n")
        view = self.native("\n{}:\n  1: edited\n  2  context\n\n{}:\n  1: last edited  \n\n2 matches across 2 files\n".format(first, last))
        self.apply(view)
        self.assertEqual(first.read_text(), "edited\ncontext\n")
        self.assertEqual(last.read_text(), "last edited  \n")

    def test_empty_native_results_do_not_crash(self):
        self.apply(self.native("Searching 0 files...\n\n0 matches\n"))
        sublime.error_message.assert_not_called()

    def test_native_path_aliases_merge_edits(self):
        path = self.file("a.txt", "first\nsecond\n")
        view = self.native("\n{}:\n  1: first edited\n\n~/a.txt:\n  2: second edited\n\n2 matches across 2 files\n".format(path))
        with patch.dict(os.environ, {"HOME": str(self.root), "USERPROFILE": str(self.root)}):
            self.apply(view)
        self.assertEqual(path.read_text(), "first edited\nsecond edited\n")

    def test_conflicting_native_edits_to_same_line_are_rejected(self):
        path = self.file("a.txt", "old\n")
        view = self.native("\n{}:\n  1: edit one\n\n~/a.txt:\n  1: edit two\n\n2 matches across 2 files\n".format(path))
        with patch.dict(os.environ, {"HOME": str(self.root), "USERPROFILE": str(self.root)}):
            self.apply(view)
        self.assertEqual(path.read_text(), "old\n")
        self.assertIn("Conflicting results", sublime.error_message.call_args.args[0])

    def test_save_handles_no_view_and_plain_text_without_syntax(self):
        plugin.Save2Command(self.window).run()
        view = self.window.new_file()
        plugin.Save2Command(self.window).run()
        self.assertEqual(view.commands, ["save"])

    def test_save_recognizes_modern_syntax(self):
        path = self.file("a.txt", "old\n")
        view = self.window.new_file()
        view.set_syntax_file("Packages/Default/Find Results.sublime-syntax")
        view.text = "\n{}:\n  1: new\n\n1 match in 1 file\n".format(path)
        plugin.Save2Command(self.window).run()
        self.assertEqual(path.read_text(), "new\n")
        self.assertTrue(plugin.FindResultsApplyChangesCommand(view).is_enabled())

    def test_import_groups_paths_and_deduplicates_matching_lines(self):
        first = self.file("src/a file.txt", "old: value\n123: text\n")
        second = self.file("other.txt", "héllo\n")
        text, originals = plugin.grep_to_find_results(
            "src/a file.txt:2:123: text\nother.txt:1:héllo\nsrc/a file.txt:1:old: value\nsrc/a file.txt:1:old: value\n",
            str(self.root),
        )
        self.assertEqual(text.count(str(first) + ":\n"), 1)
        self.assertIn("  1: old: value\n  2: 123: text\n", text)
        self.assertTrue(text.endswith("3 matches across 2 files\n"))
        self.assertEqual(set(originals), {str(first), str(second)})

    def test_grep_import_and_repeated_saves(self):
        first = self.file("a.txt", "old\nuntouched\n")
        second = self.file("b.txt", "old\n")
        view = self.import_grep("a.txt:1:old\nb.txt:1:old\n")
        self.assertEqual(view.name(), "Find Results")
        view.text = view.text.replace(": old", ": new")
        plugin.Save2Command(self.window).run()
        self.assertEqual(first.read_text(), "new\nuntouched\n")
        self.assertEqual(second.read_text(), "new\n")
        view.text = view.text.replace(": new", ": newer")
        plugin.Save2Command(self.window).run()
        self.assertEqual(first.read_text(), "newer\nuntouched\n")
        sublime.error_message.assert_not_called()

    def test_import_expands_home_paths(self):
        path = self.file("a.txt", "old\n")
        with patch.dict(os.environ, {"HOME": str(self.root), "USERPROFILE": str(self.root)}):
            view = self.import_grep("~/a.txt:1:old\n")
        self.assertIn(str(path), view.text)

    def test_import_prompt_uses_original_buffer(self):
        self.file("a.txt", "old\n")
        self.window.new_file().text = "a.txt:1:old\n"
        plugin.GrepResultsToFindResultsCommand(self.window).run()
        args = self.window.show_input_panel.call_args.args
        self.assertEqual(args[1], str(self.root))
        self.window.new_file().text = "another buffer"
        args[2](str(self.root))
        self.assertIn("  1: old", self.window.active_view().text)

    def test_malformed_colored_and_empty_results_are_rejected(self):
        self.file("a.txt", "old\n")
        for text in ("", "not grep", "a.txt:0:old", "a.txt:1:\x1b[31mold", "a.txt:1:old\nbad line"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                plugin.grep_to_find_results(text, str(self.root))

    def test_bad_working_directories_are_rejected(self):
        for directory in ("", str(self.root / "missing")):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                plugin.grep_to_find_results("a.txt:1:old", directory)

    def test_stale_and_out_of_range_results_are_rejected_at_import(self):
        self.file("a.txt", "current\n")
        for text in ("a.txt:1:stale", "a.txt:100:current"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                plugin.grep_to_find_results(text, str(self.root))

    def test_invalid_import_does_not_create_a_results_tab(self):
        source = self.window.new_file()
        source.text = "missing.txt:1:old\n"
        plugin.GrepResultsToFindResultsCommand(self.window).run(base_dir=str(self.root))
        self.assertIs(self.window.active_view(), source)
        sublime.error_message.assert_called_once()

    def test_stale_source_aborts_before_writing_other_files(self):
        first = self.file("a.txt", "old\n")
        second = self.file("z.txt", "old\n")
        view = self.import_grep("a.txt:1:old\nz.txt:1:old\n")
        view.text = view.text.replace(": old", ": new")
        second.write_text("external edit\n")
        self.apply(view)
        self.assertEqual(first.read_text(), "old\n")
        self.assertEqual(second.read_text(), "external edit\n")
        self.assertIn("Source changed", sublime.error_message.call_args.args[0])

    def test_dirty_open_source_is_not_overwritten(self):
        path = self.file("a.txt", "old\n")
        view = self.import_grep("a.txt:1:old\n")
        view.text = view.text.replace(": old", ": new")
        self.window.open_views.append(View(filename=str(path), dirty=True))
        self.apply(view)
        self.assertEqual(path.read_text(), "old\n")
        self.assertIn("Save the source file", sublime.error_message.call_args.args[0])

    def test_missing_native_file_aborts_before_any_writes(self):
        path = self.file("a.txt", "old\n")
        view = self.native("\n{}:\n  1: new\n\n{}:\n  1: new\n\n2 matches across 2 files\n".format(path, self.root / "missing.txt"))
        self.apply(view)
        self.assertEqual(path.read_text(), "old\n")
        sublime.error_message.assert_called_once()

    def test_out_of_range_native_result_does_not_write(self):
        path = self.file("a.txt", "old\n")
        self.apply(self.native("\n{}:\n  999: new\n\n1 match in 1 file\n".format(path)))
        self.assertEqual(path.read_text(), "old\n")
        sublime.error_message.assert_called_once()

    def test_crlf_and_missing_final_newline_are_preserved(self):
        path = self.file("a.txt", "old\r\nlast")
        view = self.import_grep("a.txt:1:old\r\na.txt:2:last\n")
        view.text = view.text.replace(": old", ": new").replace(": last", ": changed")
        self.apply(view)
        self.assertEqual(path.read_bytes(), b"new\r\nchanged")

    def test_multiline_edit_keeps_other_matches_and_blocks_reapplying(self):
        path = self.file("a.txt", "first\r\nsecond\r\n")
        view = self.import_grep("a.txt:1:first\na.txt:2:second\n")
        view.text = view.text.replace(": first", ": first edited\ninserted").replace(": second", ": second edited")
        self.apply(view)
        expected = b"first edited\r\ninserted\r\nsecond edited\r\n"
        self.assertEqual(path.read_bytes(), expected)
        self.apply(view)
        self.assertEqual(path.read_bytes(), expected)
        sublime.message_dialog.assert_called_once()

    def test_palette_and_keybindings_use_existing_commands(self):
        root = Path(__file__).resolve().parents[1]
        commands = json.loads((root / "Default.sublime-commands").read_text())
        self.assertEqual({entry["command"] for entry in commands}, {
            "grep_results_to_find_results", "find_results_apply_changes",
            "find_results_live_search", "find_results_live_export",
        })
        for platform in ("OSX", "Linux", "Windows"):
            binding = json.loads((root / ("Default (%s).sublime-keymap" % platform)).read_text())
            self.assertEqual(binding[0]["command"], "save2")

    def test_real_grep_and_ripgrep_output_round_trip(self):
        for tool, args in (("grep", ["-nH", "old", "a file.txt"]),
                           ("rg", ["--no-heading", "--color", "never", "-nH", "old", "a file.txt"])):
            with self.subTest(tool=tool):
                if not shutil.which(tool):
                    self.skipTest(tool + " is not installed")
                path = self.file("a file.txt", "old: value\nunchanged\nold again\n")
                output = subprocess.check_output([tool] + args, cwd=self.root, text=True)
                view = self.import_grep(output)
                view.text = view.text.replace(": old", ": new")
                self.apply(view)
                self.assertEqual(path.read_text(), "new: value\nunchanged\nnew again\n")
                sublime.error_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
