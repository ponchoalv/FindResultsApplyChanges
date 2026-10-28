import io
import json
from pathlib import Path
import shutil
import tempfile
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

    def id(self):
        return id(self)

    def create_io_panel(self, name, on_input, unlisted=False):
        self.on_submit = on_input
        self.output, self.input = LiveView(self), LiveView(self)
        return self.output, self.input

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

    def session(self, query="#"):
        session = plugin.LiveSearch(self.window, str(self.root), shutil.which("rg"))
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
        self.window.on_submit(session.query)
        results = self.window.active_view()
        self.assertEqual(results.name(), "Find Results")
        self.assertTrue(session.closed)
        self.assertIn("2 matches across 2 files", results.text)
        results.text = results.text.replace(": old keep", ": new keep")
        plugin.Save2Command(self.window).run()
        self.assertEqual(first.read_text(), "new keep\nold skip\n")
        self.assertEqual(second.read_text(), "new keep\n")
        sublime.error_message.assert_not_called()

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
        with patch.object(plugin.shutil, "which", return_value=None), patch.object(plugin.sys, "platform", "linux"):
            command.run(directory=str(self.root))
        self.assertIn("Install ripgrep", sublime.error_message.call_args.args[0])
        self.assertEqual(plugin.live_searches, {})

    def test_live_keys_are_scoped_to_the_search_input(self):
        path = Path(__file__).resolve().parents[1] / "Default.sublime-keymap"
        bindings = json.loads(path.read_text())
        self.assertEqual({tuple(binding["keys"]) for binding in bindings}, {("up",), ("down",), ("ctrl+enter",), ("escape",)})
        for binding in bindings:
            self.assertEqual(binding["context"], [{"key": "setting.find_results_live_input", "operand": True}])
