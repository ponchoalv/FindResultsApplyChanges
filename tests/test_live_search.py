import io
import json
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_find_results import Region, View, Window, plugin, sublime


class Selection(list):
    def add(self, region):
        self.append(region)

    def add_all(self, regions):
        self.extend(regions)


class LiveView(View):
    def __init__(self, window, **kwargs):
        super().__init__(**kwargs)
        self.owner, self.valid, self.transient = window, True, False
        self.selection, self.viewport = Selection([Region(0, 0)]), (0, 0)

    def sel(self):
        return self.selection

    def viewport_position(self):
        return self.viewport

    def set_viewport_position(self, position, animate=False):
        self.viewport = position

    def is_valid(self):
        return self.valid

    def window(self):
        return self.owner

    def sheet(self):
        return self

    def is_transient(self):
        return self.transient

    def close(self):
        self.valid = False

    def text_point(self, row, column):
        return sum(len(line) for line in self.text.splitlines(True)[:row]) + column

    def line(self, point):
        start = self.text.rfind("\n", 0, point) + 1
        end = self.text.find("\n", point)
        return Region(start, len(self.text) if end < 0 else end)

    def full_line(self, point):
        region = self.line(point)
        return Region(region.a, min(region.b + 1, self.size()))

    def rowcol(self, point):
        return self.text.count("\n", 0, point), point - self.text.rfind("\n", 0, point) - 1

    def find(self, pattern, point, flags=0):
        if flags & sublime.LITERAL:
            pattern = re.escape(pattern)
        if flags & sublime.WHOLEWORD:
            pattern = r"\b(?:" + pattern + r")\b"
        try:
            match = re.compile(pattern, re.MULTILINE | (re.I if flags & sublime.IGNORECASE else 0)).search(self.text, point)
        except re.error:
            return Region(-1, -1)
        return Region(*match.span()) if match else Region(-1, -1)

    def show(self, region):
        self.visible = region

    def set_read_only(self, value):
        self.read_only = value

    def replace(self, edit, region, text):
        self.text = self.text[:region.a] + text + self.text[region.b:]

    def run_command(self, command, args=None):
        if command == "insert":
            self.text = args["characters"]
        elif command == "find_results_live_render":
            plugin.FindResultsLiveRenderCommand(self).run(None, args["text"])
        else:
            super().run_command(command, args)


class LiveWindow(Window):
    def __init__(self, directory):
        super().__init__(directory)
        self.panel, self.focused = None, None
        self.previewed = []
        self.output_panels, self.project = {}, None

    def id(self):
        return id(self)

    def create_io_panel(self, name, on_input, unlisted=False):
        self.on_submit = on_input
        self.output, self.input = LiveView(self), LiveView(self)
        return self.output, self.input

    def create_output_panel(self, name, unlisted=False):
        self.output_panels[name] = LiveView(self)
        return self.output_panels[name]

    def destroy_output_panel(self, name):
        self.output_panels.pop(name).valid = False

    def project_data(self):
        return self.project

    def project_file_name(self):
        return str(self.directory / "test.sublime-project")

    def new_file(self):
        self.current = LiveView(self)
        self.open_views.append(self.current)
        return self.current

    def focus_view(self, view):
        self.focused = view

    def active_panel(self):
        return self.panel

    def run_command(self, command, args=None):
        self.panel = args["panel"] if command == "show_panel" else None

    def open_file(self, location, flags):
        path, line = location.rsplit(":", 1)
        self.previewed.append((path, int(line), flags))
        view = next((view for view in self.open_views if view.file_name() == path), None)
        if view is None:
            view = LiveView(self, filename=path)
            view.transient = True
            self.open_views.append(view)
        return view


class Process:
    def __init__(self, output, code=0):
        self.stdout = io.BytesIO(output)
        self.code, self.killed = code, False

    def poll(self):
        return None

    def kill(self):
        self.killed, self.code = True, -9

    def wait(self):
        return self.code


class LiveSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.window = LiveWindow(self.root)
        self.window.new_file()
        for mock in (sublime.error_message, sublime.message_dialog, sublime.status_message):
            mock.reset_mock()
        sublime.windows.return_value = [self.window]
        sublime.ENCODED_POSITION, sublime.TRANSIENT = 1, 4
        sublime.LITERAL, sublime.IGNORECASE, sublime.WHOLEWORD = 1, 2, 4
        self.preferences = View()
        settings = patch.object(sublime, "load_settings", return_value=self.preferences, create=True)
        settings.start()
        self.addCleanup(settings.stop)
        plugin.live_searches.clear()
        self.callbacks = []
        timeout = patch.object(sublime, "set_timeout", self.schedule)
        timeout.start()
        self.addCleanup(timeout.stop)
        self.addCleanup(plugin.plugin_unloaded)

    def file(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents.encode("utf8"))
        return path

    def schedule(self, callback, delay=0):
        self.callbacks.append((callback, delay))

    def finish(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback, delay in callbacks:
            if delay == 0:
                callback()

    def session(self, query="#", native=False):
        session = (plugin.SublimeLiveSearch(self.window, str(self.root), None) if native
                   else plugin.LiveSearch(self.window, str(self.root), shutil.which("rg")))
        plugin.live_searches[self.window.id()] = session
        session.open(query)
        return session

    def query(self, session, text):
        session.input.text = text
        plugin.FindResultsApplyChangesEventListener().on_modified(session.input)

    def search(self, session, text):
        self.query(session, text)
        session.search(session.pattern, session.generation)
        self.finish()

    def test_split_and_filter_query(self):
        self.assertEqual(plugin.split_live_query("#error.*#payment !test"), ("error.*", "payment !test"))
        self.assertEqual(plugin.split_live_query("plain#hash"), ("plain#hash", ""))
        results = [
            (str(self.root / "src/pay.py"), 1, "ERROR payment declined"),
            (str(self.root / "tests/pay.py"), 2, "error payment declined"),
        ]
        self.assertEqual(plugin.filter_live_results(results, '"payment declined" !tests', str(self.root)), results[:1])
        self.assertEqual(plugin.filter_live_results(results, "ERROR", str(self.root)), results[:1])
        self.assertEqual(plugin.filter_live_results(results, "error", str(self.root)), results)

    def test_options_preserve_regex_quotes_and_backslashes(self):
        pattern = r"\bcase\b '.*':"
        for query in (pattern + " -C2 -g '*.c'", "-C 2 --glob='*.c' -- " + pattern):
            with self.subTest(query=query):
                args, before, after = plugin.live_search_args(query)
                self.assertEqual(args, ["--context=2", "--glob=*.c", "-e", pattern])
                self.assertEqual((before, after), (2, 2))
        self.assertEqual(plugin.live_search_args(r"\-literal"), (["-e", "-literal"], 0, 0))
        self.assertEqual(plugin.live_search_args("-- -literal"), (["-e", "-literal"], 0, 0))
        args, _, _ = plugin.live_search_args("case -g 'a -- b.c'")
        self.assertEqual(args, ["--glob=a -- b.c", "-e", "case"])

    def test_context_flags_follow_ripgrep_semantics(self):
        self.file("src/day20/a.txt", "one\ntwo\nold hit\nfour\nfive\n")
        self.file("src/day21/b.txt", "one\ntwo\nold other\nfour\nfive\n")
        for flags, expected in (
            ("-C2", [1, 2, 3, 4, 5]),
            ("-A 2", [3, 4, 5]),
            ("--before-context=2", [1, 2, 3]),
            ("-B1 -A2", [2, 3, 4, 5]),
            ("-A1 -C2", [1, 2, 3, 4]),
            ("--context 2 --before-context=0", [3, 4, 5]),
        ):
            with self.subTest(flags=flags):
                session = self.session()
                self.search(session, "#old " + flags + "#/day20/")
                self.assertEqual(session.error, "")
                self.assertEqual(len(session.results), 2)
                self.assertEqual(len(session.filtered), 1)
                self.assertEqual([row[1] for row in session.displayed], expected)
                self.assertTrue(all("/day20/" in row[0] for row in session.displayed))
                selected = session.output.substr(session.output.sel()[0])
                self.assertIn("a.txt:3:old hit", selected)
                session.close()

    def test_overlapping_context_exports_once_and_context_can_be_edited(self):
        path = self.file("a.txt", "intro\nold keep one\nold skip\nold keep two\ntail\noutside\n")
        session = self.session()
        self.search(session, "#old -C1#keep")
        self.assertEqual(len(session.filtered), 2)
        self.assertEqual([row[1] for row in session.displayed], [1, 2, 3, 4, 5])
        self.assertIn("a.txt-3-old skip", session.output.text)
        generation, lines = session.generation, session.lines
        self.query(session, "#old -C1#two")
        self.assertEqual([row[1] for row in session.displayed], [3, 4, 5])
        self.assertEqual(session.generation, generation)
        self.assertIs(session.lines, lines)
        self.query(session, "#old -C1#keep")
        session.move(1)
        self.assertIn("a.txt:4:old keep two", session.output.substr(session.output.sel()[0]))
        session.export()
        view = self.window.active_view()
        self.assertEqual(view.text.count("  3  old skip\n"), 1)
        self.assertIn("2 matches across 1 files", view.text)
        view.text = view.text.replace(": old keep", ": new keep").replace("  3  old skip", "  3  new context")
        plugin.Save2Command(self.window).run()
        self.assertEqual(path.read_text(), "intro\nnew keep one\nnew context\nnew keep two\ntail\noutside\n")
        sublime.error_message.assert_not_called()

    def test_search_flags_work_with_case_globs_and_ignored_files(self):
        kept = self.file("src/a.py", "ERROR [value]\nerror value\nERROR [values]\n")
        self.file("src/a.txt", "ERROR [value]\n")
        hidden = self.file(".hidden.py", "ERROR [value]\n")
        ignored = self.file("ignored.py", "ERROR [value]\n")
        self.file(".ignore", "ignored.py\n")
        session = self.session()
        self.search(session, "#[value] -Fi -g '*.py' --hidden --no-ignore#")
        self.assertEqual(session.error, "")
        self.assertEqual({row[0] for row in session.filtered}, {str(kept), str(hidden), str(ignored)})
        self.assertTrue(all(row[1] == 1 for row in session.filtered))

    def test_explicit_patterns_and_option_values_starting_with_dashes(self):
        self.file("a.txt", "-literal\nother\n")
        self.file("patterns", "-literal\n")
        for query in ("#-e -literal -g '*.txt'#", "#-f patterns -g '*.txt'#", "#-F -- -literal#"):
            with self.subTest(query=query):
                session = self.session()
                self.search(session, query)
                self.assertEqual(session.error, "")
                self.assertTrue(session.filtered)
                self.assertTrue(all(row[2] == "-literal" for row in session.filtered))
                session.close()

    def test_invalid_or_incompatible_flags_are_reported_before_spawning(self):
        session = self.session()
        for flags in ("-C", "-A nope", "-B-1", "-C2 --pre=cat", "-U", "-io", "--replace=x", "--count", "--no-json", "--glob='unclosed"):
            with self.subTest(flags=flags), patch.object(plugin.subprocess, "Popen") as popen:
                self.search(session, "#old " + flags + "#")
                self.assertTrue(session.error)
                self.assertFalse(session.searching)
                self.assertEqual(session.filtered, [])
                session.export()
                self.assertFalse(session.closed)
                popen.assert_not_called()

    def test_flags_in_second_section_remain_literal_filters(self):
        self.file("a.txt", "old -C2\nold other\n")
        session = self.session()
        self.search(session, "#old#-C2")
        self.assertEqual((session.before, session.after), (0, 0))
        self.assertEqual([row[2] for row in session.displayed], ["old -C2"])

    def test_context_lines_count_towards_search_cap(self):
        self.file("a.txt", "context\nold\ncontext\n")
        session = self.session()
        with patch.object(plugin, "MAX_LIVE_RESULTS", 2):
            self.search(session, "#old -C100000#")
            session.export()
        self.assertTrue(session.truncated)
        self.assertEqual(len(session.lines), 2)
        self.assertFalse(session.closed)
        self.assertIn("truncated", sublime.error_message.call_args.args[0])

    def test_changed_context_line_prevents_export(self):
        path = self.file("a.txt", "before\nold\nafter\n")
        session = self.session()
        self.search(session, "#old -C1#")
        path.write_text("changed\nold\nafter\n")
        session.export()
        self.assertFalse(session.closed)
        self.assertIn("out of date", sublime.error_message.call_args.args[0])
        self.assertEqual(path.read_text(), "changed\nold\nafter\n")

    def test_real_search_preview_export_edit_and_save(self):
        first = self.file("src/a file.txt", "old keep\nold skip\n")
        second = self.file("b.txt", "old keep\n")
        self.file("ignored.txt", "old keep\n")
        self.file(".ignore", "ignored.txt\n")
        session = self.session()
        self.search(session, "#old#keep")
        self.assertEqual(len(session.filtered), 2)
        session.move(1)
        self.assertEqual(len(self.window.previewed), 2)
        self.assertIs(self.window.focused, session.input)
        plugin.FindResultsLiveExportCommand(self.window).run()
        results = self.window.active_view()
        self.assertEqual(results.name(), "Find Results")
        self.assertTrue(session.closed)
        self.assertIn("2 matches across 2 files", results.text)
        results.text = results.text.replace(": old keep", ": new keep")
        plugin.Save2Command(self.window).run()
        self.assertEqual(first.read_text(), "new keep\nold skip\n")
        self.assertEqual(second.read_text(), "new keep\n")
        sublime.error_message.assert_not_called()

    def test_native_search_context_filter_export_and_save_without_rg(self):
        first = self.file("src/day20/a.c", "// before\ncase '^': old();\n// between\ncase 'N': old();\n// after\n")
        other = self.file("src/day21/a.c", "case 'N': old();\n")
        session = self.session(native=True)
        with patch.object(plugin.subprocess, "Popen", side_effect=AssertionError("Native search must not run rg")):
            self.search(session, "#case '.*': -C1#/day20/")
        self.assertEqual(session.error, "")
        self.assertEqual(len(session.results), 3)
        self.assertEqual(len(session.filtered), 2)
        self.assertEqual([row[1] for row in session.displayed], [1, 2, 3, 4, 5])
        self.assertEqual(self.window.output_panels, {})
        session.move(1)
        self.assertIs(self.window.focused, session.input)
        session.export()
        view = self.window.active_view()
        self.assertIn("2 matches across 1 files", view.text)
        view.text = view.text.replace("old()", "new()").replace("// between", "// edited context")
        plugin.Save2Command(self.window).run()
        self.assertEqual(first.read_text(), "// before\ncase '^': new();\n// edited context\ncase 'N': new();\n// after\n")
        self.assertEqual(other.read_text(), "case 'N': old();\n")
        sublime.error_message.assert_not_called()

    def test_native_case_literal_word_and_glob_options(self):
        self.file("a.py", "VALUE [x]\nvalue [x]\nvalues [x]\n")
        self.file("a.txt", "VALUE [x]\n")
        session = self.session(native=True)
        for query, expected in (
            ("#[x] -F -s -g '*.py'#", [1, 2, 3]),
            ("#VALUE -w -g '*.py'#", [1]),
            ("#VALUE -iw -g '*.py'#", [1, 2]),
            ("#value -w -g '*.py'#", [1, 2]),
            ("#value -sw -g '*.py'#", [2]),
        ):
            with self.subTest(query=query):
                self.search(session, query)
                self.assertEqual(session.error, "")
                self.assertEqual([row[1] for row in session.filtered], expected)

    def test_native_file_selection_uses_preferences_and_project_patterns(self):
        kept = self.file("src/keep.py", "old\n")
        for name in (".git/secret.py", "build/skip.py", "vendor/skip.py", "src/skip.log",
                     "src/image.png", "src/bundle.min.js", "src/generated.py", "src/no.txt"):
            self.file(name, "old\n")
        self.preferences.set("folder_exclude_patterns", [".git", "build"])
        self.preferences.set("file_exclude_patterns", ["*.log"])
        self.preferences.set("binary_file_patterns", ["*.png"])
        self.preferences.set("index_exclude_patterns", ["*.min.js"])
        self.window.project = {"folders": [{
            "path": ".", "folder_exclude_patterns": ["//vendor/"],
            "file_exclude_patterns": ["generated.py"], "file_include_patterns": ["*.py"],
        }]}
        self.assertEqual(list(plugin.sublime_search_files(self.window, str(self.root))), [str(kept)])
        self.assertEqual(list(plugin.sublime_search_files(self.window, str(self.root / "src"))), [str(kept)])

    def test_native_sublime_path_patterns(self):
        root = str(self.root)
        yes = plugin.sublime_path_matches
        self.assertTrue(yes(root + "/src/test.py", ["*.py"], root))
        self.assertTrue(yes(root + "/src/sub/a.py", ["//src/"], root))
        self.assertFalse(yes(root + "/nested/src/a.py", ["//src/"], root))
        self.assertFalse(yes(root + "/src_sub/a.py", ["//src/"], root))
        self.assertFalse(yes(root + "/a1.py", ["a[12].py"], root))
        self.assertTrue(yes(root + "/nested/src/a.py", ["src/a.py"], root))

    def test_native_suffix_filter_reuses_matches_and_context(self):
        self.file("a.txt", "old first\ncontext\nold second\n")
        session = self.session(native=True)
        self.search(session, "#old -C1#first")
        generation, lines = session.generation, session.lines
        with patch.object(self.window, "create_output_panel") as scanner:
            self.query(session, "#old -C1#second")
        scanner.assert_not_called()
        self.assertEqual(session.generation, generation)
        self.assertIs(session.lines, lines)
        self.assertEqual([row[1] for row in session.displayed], [2, 3])

    def test_native_zero_width_patterns_and_repeated_hits_do_not_duplicate_lines(self):
        self.file("a.txt", "old old\nother\n")
        session = self.session(native=True)
        for query, numbers in (("#old#", [1]), ("#^#", [1, 2]), ("#$#", [1, 2]), ("#old|$#", [1, 2])):
            with self.subTest(query=query):
                self.search(session, query)
                self.assertEqual([row[1] for row in session.results], numbers)
                self.assertEqual(self.window.output_panels, {})

    def test_native_skips_binary_invalid_utf8_and_oversized_files(self):
        self.file("a.txt", "old\n")
        self.file("binary.txt", "old\0\n")
        self.file("large.txt", "old" * 20)
        (self.root / "bad.txt").write_bytes(b"old\xff\n")
        session = self.session(native=True)
        with patch.object(plugin, "MAX_SUBLIME_FILE_BYTES", 10):
            self.search(session, "#old#")
        self.assertEqual(len(session.results), 1)
        self.assertEqual(session.skipped, 3)
        self.assertIn("3 unreadable", session.output.text)

    def test_native_context_cap_cannot_export_partial_or_missing_hits(self):
        self.file("a.txt", "before\ncontext\nold\nafter\n")
        session = self.session(native=True)
        with patch.object(plugin, "MAX_LIVE_RESULTS", 2):
            self.search(session, "#old -C100000#")
        self.assertTrue(session.truncated)
        self.assertEqual(session.filtered, [])
        self.assertIn("exceeds", session.output.text)
        self.assertEqual(len(session.lines), 2)
        session.export()
        self.assertFalse(session.closed)

    def test_native_changed_source_blocks_export_and_crlf_is_preserved(self):
        path = self.file("a.txt", "old\r\ncontext\r\n")
        session = self.session(native=True)
        self.search(session, "#old -A1#")
        path.write_bytes(b"old\r\nexternal\r\n")
        session.export()
        self.assertFalse(session.closed)
        self.assertIn("out of date", sublime.error_message.call_args.args[0])
        self.search(session, "#old -C1#")
        session.export()
        view = self.window.active_view()
        view.text = view.text.replace(": old", ": new")
        plugin.Save2Command(self.window).run()
        self.assertEqual(path.read_bytes(), b"new\r\nexternal\r\n")

    def test_native_cancel_during_scan_discards_results_and_removes_hidden_panel(self):
        self.file("a.txt", "old\n")
        session = self.session(native=True)
        original_find = LiveView.find

        def cancel(view, *args):
            session.close()
            return original_find(view, *args)

        with patch.object(LiveView, "find", cancel):
            self.search(session, "#old#")
        self.assertTrue(session.closed)
        self.assertEqual(session.results, [])
        self.assertEqual(self.window.output_panels, {})

    def test_native_invalid_regex_and_no_matches_leave_nothing_to_export(self):
        self.file("a.txt", "old\n")
        session = self.session(native=True)
        for query in ("#(#", "#missing#"):
            self.search(session, query)
            self.assertEqual(session.results, [])
            self.assertFalse(session.searching)
            self.assertEqual(self.window.output_panels, {})
            session.export()
            self.assertFalse(session.closed)

    def test_native_command_needs_no_executable_and_switches_from_rg(self):
        self.file("a.txt", "old\n")
        previous = self.session()
        origin = previous.origin
        self.search(previous, "#old#")
        with patch.object(plugin.shutil, "which", side_effect=AssertionError("Native search must not look for rg")):
            plugin.FindResultsLiveSublimeCommand(self.window).run(directory=str(self.root))
        self.assertTrue(previous.closed)
        session = plugin.live_searches[self.window.id()]
        self.assertIsInstance(session, plugin.SublimeLiveSearch)
        self.assertIs(session.origin, origin)
        for query in ("#old -P#", "#old -t py#", "#old --no-ignore#"):
            self.search(session, query)
            self.assertIn("Live Ripgrep", session.error)
            self.assertEqual(session.filtered, [])

    def test_filters_reuse_results_without_starting_another_process(self):
        self.file("a.txt", "old first\nold second\n")
        session = self.session()
        self.search(session, "#old#first")
        generation, results = session.generation, session.results
        with patch.object(plugin.subprocess, "Popen") as popen:
            self.query(session, "#old#second")
        self.assertEqual(session.generation, generation)
        self.assertIs(session.results, results)
        self.assertEqual(session.filtered[0][1], 2)
        popen.assert_not_called()

    def test_native_submit_exports_after_sublime_clears_the_input(self):
        self.file("a.txt", "old\n")
        session = self.session()
        self.search(session, "#old#")
        submitted = session.query
        session.input.text = ""
        plugin.FindResultsApplyChangesEventListener().on_modified(session.input)
        self.window.on_submit(submitted)
        self.finish()
        self.assertTrue(session.closed)
        self.assertIn("  1: old", self.window.active_view().text)

    def test_deleting_query_clears_results_without_exporting(self):
        self.file("a.txt", "old\n")
        session = self.session()
        self.search(session, "#old#")
        self.query(session, "")
        self.finish()
        self.assertEqual(session.filtered, [])
        self.assertFalse(session.closed)

    def test_invalid_regex_no_matches_and_unclosed_filter_quote(self):
        self.file("a.txt", "old\n")
        session = self.session()
        self.search(session, "#(#")
        self.assertIn("regex", session.error)
        session.export()
        self.assertFalse(session.closed)
        self.search(session, "#no-match#")
        self.assertEqual(session.filtered, [])
        self.assertEqual(session.error, "")
        self.search(session, '#old#"unclosed')
        self.assertTrue(session.filter_error)
        session.export()
        self.assertFalse(session.closed)

    def test_truncated_search_cannot_be_exported(self):
        self.file("a.txt", "old one\nold two\n")
        session = self.session()
        with patch.object(plugin, "MAX_LIVE_RESULTS", 1):
            self.search(session, "#old#")
            session.export()
        self.assertTrue(session.truncated)
        self.assertEqual(len(session.results), 1)
        self.assertFalse(session.closed)
        self.assertIn("truncated", sublime.error_message.call_args.args[0])

    def test_debounce_discards_superseded_searches_and_kills_running_process(self):
        session = self.session()
        self.query(session, "#first#")
        process = Process(b"")
        session.process = process
        self.query(session, "#second#")
        self.assertTrue(process.killed)
        with patch.object(plugin.threading, "Thread") as worker:
            for callback, _ in self.callbacks:
                callback()
        worker.assert_called_once()
        self.assertEqual(worker.call_args.kwargs["args"], ("second", session.generation))

    def test_results_from_cancelled_search_do_not_replace_current_query(self):
        self.file("a.txt", "old\nnew\n")
        session = self.session()
        self.query(session, "#old#")
        session.search(session.pattern, session.generation)
        self.query(session, "#new#")
        self.finish()
        self.assertEqual(session.results, [])
        self.assertTrue(session.searching)

    def test_stale_process_started_during_query_change_is_killed(self):
        session = self.session()
        self.query(session, "#old#")
        generation = session.generation
        process = Process(b"")

        def popen(*args, **kwargs):
            self.query(session, "#new#")
            return process

        with patch.object(plugin.subprocess, "Popen", side_effect=popen):
            session.search("old", generation)
        self.finish()
        self.assertTrue(process.killed)
        self.assertEqual(session.pattern, "new")
        self.assertTrue(session.searching)

    def test_cancel_restores_editor_and_stops_background_work(self):
        self.file("a.txt", "old\n")
        origin = self.window.active_view()
        origin.viewport = (0, 50)
        session = self.session()
        self.search(session, "#old#")
        preview = session.preview
        origin.viewport = (0, 999)
        process = Process(b"")
        session.process = process
        plugin.FindResultsLiveCancelCommand(self.window).run()
        self.assertTrue(process.killed)
        self.assertFalse(preview.valid)
        self.assertIs(self.window.focused, origin)
        self.assertEqual(origin.viewport, (0, 50))
        self.assertNotIn(self.window.id(), plugin.live_searches)

    def test_pending_search_does_not_export_previous_matches(self):
        self.file("a.txt", "old\n")
        session = self.session()
        self.search(session, "#old#")
        self.query(session, "#new#")
        session.export()
        self.assertFalse(session.closed)
        self.assertEqual(session.filtered, [])

    def test_source_change_between_search_and_export_is_rejected(self):
        path = self.file("a.txt", "old\n")
        session = self.session()
        self.search(session, "#old#")
        path.write_text("external edit\n")
        session.export()
        self.assertFalse(session.closed)
        self.assertIn("out of date", sublime.error_message.call_args.args[0])
        self.assertEqual(path.read_text(), "external edit\n")

    def test_panel_hide_and_plugin_unload_cancel_searches(self):
        session = self.session()
        plugin.FindResultsApplyChangesEventListener().on_window_command(self.window, "hide_panel", None)
        self.assertTrue(session.closed)
        session = self.session()
        plugin.plugin_unloaded()
        self.assertTrue(session.closed)

    def test_parser_rejects_non_utf8_records_and_reaps_process(self):
        event = {"type": "match", "data": {"path": {"bytes": "YQ=="}, "lines": {"text": "old\n"}, "line_number": 1}}
        process = Process(json.dumps(event).encode() + b"\n")
        with self.assertRaises(ValueError):
            plugin.read_live_results(process, str(self.root))
        self.assertTrue(process.killed)
        self.assertTrue(process.stdout.closed)

    def test_missing_ripgrep_and_directory_are_reported(self):
        command = plugin.FindResultsLiveSearchCommand(self.window)
        command.run(directory=str(self.root / "missing"))
        self.assertIn("directory does not exist", sublime.error_message.call_args.args[0])
        for platform in ("linux", "win32"):
            with self.subTest(platform=platform), patch.object(plugin.shutil, "which", return_value=None), patch.object(plugin.sys, "platform", platform):
                command.run(directory=str(self.root))
                self.assertIn("Install ripgrep", sublime.error_message.call_args.args[0])
        self.assertEqual(plugin.live_searches, {})

    def test_windows_launch_works_without_newer_python_constants(self):
        session = self.session()
        session.executable = r"C:\Program Files\ripgrep\rg.exe"
        self.query(session, "#old#")
        legacy = SimpleNamespace(
            Popen=Mock(return_value=Process(b"")), DEVNULL=plugin.subprocess.DEVNULL,
            PIPE=plugin.subprocess.PIPE, STDOUT=plugin.subprocess.STDOUT,
        )
        with patch.object(plugin.sys, "platform", "win32"), patch.object(plugin, "subprocess", legacy):
            session.search(session.pattern, session.generation)
        self.finish()
        self.assertEqual(session.error, "")
        self.assertFalse(session.searching)
        popen = legacy.Popen
        self.assertEqual(popen.call_args.args[0][0], session.executable)
        self.assertEqual(popen.call_args.kwargs["creationflags"], 0x08000000)
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_live_keys_are_scoped_to_the_search_input(self):
        path = Path(__file__).resolve().parents[1] / "Default.sublime-keymap"
        bindings = json.loads(path.read_text())
        self.assertEqual({tuple(binding["keys"]) for binding in bindings}, {("enter",), ("up",), ("down",), ("ctrl+enter",), ("escape",)})
        for binding in bindings:
            self.assertEqual(binding["context"], [{"key": "setting.find_results_live_input", "operand": True}])
