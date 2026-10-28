# coding=utf8
import sublime
import sublime_plugin
import re
import os
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import threading

debug = False
LIVE_PANEL = "find_results_live"
MAX_LIVE_RESULTS = 10000
live_searches = {}


def is_find_results(view):
    return view is not None and (
        view.name() == "Find Results"
        or "Find Results." in (view.settings().get("syntax") or "")
    )


def read_file(filepath):
    with open(filepath, "r", newline="", encoding="utf8") as source:
        return source.read()


def grep_to_find_results(text, base_dir, matches=None):
    if not base_dir:
        raise ValueError("Enter the directory where grep was run.")
    base_dir = os.path.abspath(os.path.expanduser(base_dir))
    if not os.path.isdir(base_dir):
        raise ValueError("The grep working directory does not exist: " + base_dir)
    changes = {}
    originals = {}
    source_lines = {}
    for row, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if not line:
            continue
        match = re.match(r"^(.+?):([1-9][0-9]*):(.*)$", line)
        if not match or "\x1b" in line:
            raise ValueError(
                "Expected uncoloured file:line:text at result line {0}. "
                "Use rg --no-heading --color never -nH, or grep -nH.".format(row)
            )
        filename, number, content = match.groups()
        filename = os.path.expanduser(filename)
        if not os.path.isabs(filename):
            filename = os.path.join(base_dir, filename)
        filename = os.path.realpath(filename)
        if filename not in originals:
            original = read_file(filename)
            originals[filename] = hashlib.sha256(original.encode("utf8")).hexdigest()
            source_lines[filename] = original.split("\n")
        lines = source_lines[filename]
        number = int(number)
        if number > len(lines) or lines[number - 1].rstrip("\r") != content:
            raise ValueError("Grep result is out of date: {0}:{1}".format(filename, number))
        changes.setdefault(filename, {})[number] = content
    if not changes:
        raise ValueError("No grep results to import.")
    blocks = []
    count = 0
    for filename in sorted(changes):
        blocks.append("\n" + filename + ":\n")
        for number in sorted(changes[filename]):
            matched = matches is None or (filename, number) in matches
            count += matched
            blocks.append("  {0}{1}{2}\n".format(
                number, ": " if matched else "  ", changes[filename][number]
            ))
    blocks.append(
        "\n{0} matches across {1} files\n".format(count, len(changes))
    )
    return "".join(blocks), originals


class GrepResultsToFindResultsCommand(sublime_plugin.WindowCommand):
    def run(self, base_dir=None):
        source = self.window.active_view()
        if source is None:
            return
        text = source.substr(sublime.Region(0, source.size()))
        if base_dir is None:
            folders = self.window.folders()
            default = (
                folders[0] if len(folders) == 1
                else os.path.dirname(source.file_name() or "")
            )
            self.window.show_input_panel(
                "Directory where grep was run",
                default,
                lambda directory: self.import_results(text, directory),
                None,
                None,
            )
        else:
            self.import_results(text, base_dir)

    def import_results(self, text, base_dir, matches=None):
        try:
            results, originals = grep_to_find_results(text, base_dir, matches)
        except (OSError, UnicodeError, ValueError) as error:
            sublime.error_message(str(error))
            return
        view = self.window.new_file()
        view.set_name("Find Results")
        view.set_syntax_file("Packages/Default/Find Results.hidden-tmLanguage")
        view.set_scratch(True)
        view.settings().set("FindResultsApplyChanges-originals", originals)
        view.run_command("append", {"characters": results})
        return view


def split_live_query(text):
    if text.startswith("#"):
        pattern, _, filters = text[1:].partition("#")
        return pattern, filters
    return text, ""


def live_search_args(query):
    pattern, options = query, ""
    first = re.search(r"(^| )-", query)
    if first:
        pattern, options = query[:first.start()].rstrip(), query[first.end() - 1:]
    switches = set((
        "case-sensitive ignore-case smart-case fixed-strings word-regexp line-regexp "
        "invert-match crlf no-crlf no-unicode pcre2 no-pcre2 no-pcre2-unicode "
        "auto-hybrid-regex text no-text mmap no-mmap "
        "stop-on-nonmatch follow no-follow hidden no-hidden glob-case-insensitive "
        "ignore-file-case-insensitive no-ignore no-ignore-dot no-ignore-exclude "
        "no-ignore-files no-ignore-global no-ignore-parent no-ignore-vcs "
        "no-require-git require-git one-file-system unrestricted no-ignore-messages"
    ).split())
    valued = set((
        "after-context before-context context glob iglob type type-not type-add type-clear "
        "ignore-file max-depth max-filesize max-count threads engine dfa-size-limit "
        "regex-size-limit sort sortr regexp file"
    ).split())
    short = {
        "A": "after-context", "B": "before-context", "C": "context", "g": "glob",
        "i": "ignore-case", "s": "case-sensitive", "S": "smart-case", "F": "fixed-strings",
        "w": "word-regexp", "x": "line-regexp", "v": "invert-match", "P": "pcre2",
        "a": "text", "L": "follow", ".": "hidden", "u": "unrestricted",
        "t": "type", "T": "type-not", "m": "max-count", "j": "threads",
        "d": "max-depth", "e": "regexp", "f": "file",
    }
    args, context = [], {"context": 0}
    tokens = shlex.shlex(options, posix=True)
    tokens.whitespace_split, tokens.commenters = True, ""
    for token in tokens:
        if token == "--":
            pattern = " ".join(part for part in (pattern, tokens.instream.read().lstrip()) if part)
            break
        elif token.startswith("--"):
            name, equals, value = token[2:].partition("=")
            flags = [(name, value if equals else None)]
        elif token.startswith("-") and len(token) > 1:
            flags = []
            for index, char in enumerate(token[1:], 2):
                name = short.get(char, char)
                value = (token[index:] or None) if name in valued else None
                flags.append((name, value))
                if name in valued:
                    break
        else:
            raise ValueError("Expected a ripgrep option, got {0}. Put the pattern before flags or after --.".format(token))
        for name, value in flags:
            if name not in switches and name not in valued:
                raise ValueError("--{0} is not supported by editable live search.".format(name))
            if name in valued:
                if value is None:
                    value = next(tokens, None)
                if value is None:
                    raise ValueError("--{0} needs a value.".format(name))
                args.append("--{0}={1}".format(name, value))
                if name in ("context", "before-context", "after-context"):
                    if not re.match(r"^[0-9]+$", value):
                        raise ValueError("--{0} needs a non-negative line count.".format(name))
                    context[name] = int(value)
            elif value is not None:
                raise ValueError("--{0} does not take a value.".format(name))
            else:
                args.append("--" + name)
    pattern = re.sub(r"(^| )\\-", r"\1-", pattern)
    if pattern:
        args += ["-e", pattern]
    elif not any(arg.startswith(("--regexp=", "--file=")) for arg in args):
        raise ValueError("Enter a pattern before the options, or after --.")
    return args, context.get("before-context", context["context"]), context.get("after-context", context["context"])


def live_result_label(result, directory, matched=True):
    filename, number, text = result
    return (":" if matched else "-").join((os.path.relpath(filename, directory), str(number), text))


def filter_live_results(results, filters, directory):
    terms = shlex.split(filters)
    sensitive = filters != filters.lower()
    if not sensitive:
        terms = [term.casefold() for term in terms]
    include = [term for term in terms if not (term.startswith("!") and len(term) > 1)]
    exclude = [term[1:] for term in terms if term.startswith("!") and len(term) > 1]
    matches = []
    for result in results:
        label = live_result_label(result, directory)
        if not sensitive:
            label = label.casefold()
        if all(term in label for term in include) and not any(term in label for term in exclude):
            matches.append(result)
    return matches


def live_context_results(matches, lines, before, after):
    if not before and not after:
        return matches
    ranges = {}
    for filename, number, _ in sorted(matches):
        spans = ranges.setdefault(filename, [])
        start, end = number - before, number + after
        if spans and start <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    positions, results = {}, []
    for (filename, number), text in sorted(lines.items()):
        spans = ranges.get(filename, [])
        index = positions.get(filename, 0)
        while index < len(spans) and number > spans[index][1]:
            index += 1
        positions[filename] = index
        if index < len(spans) and number >= spans[index][0]:
            results.append((filename, number, text))
    return results


def kill_live_process(process):
    if process is not None and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass  # The previous search may have exited between poll and kill.


def read_live_results(process, directory):
    results, lines, errors = [], {}, []
    truncated = False
    try:
        for raw in process.stdout:
            try:
                event = json.loads(raw.decode("utf8"))
            except (ValueError, UnicodeError):
                errors.append(raw.decode("utf8", "replace").rstrip()[:2000])
                continue
            if event.get("type") not in ("match", "context"):
                continue
            data = event["data"]
            filename = data["path"].get("text")
            text = data["lines"].get("text")
            if filename is None or text is None or "\n" in filename or "\r" in filename:
                raise ValueError("Live search needs UTF-8 files and filenames without newlines.")
            if len(lines) == MAX_LIVE_RESULTS or len(results) == MAX_LIVE_RESULTS:
                truncated = True
                kill_live_process(process)
                break
            filename = os.path.realpath(os.path.join(directory, filename))
            number, text = data["line_number"], text.rstrip("\n").rstrip("\r")
            lines[(filename, number)] = text
            if event["type"] == "match":
                results.append((filename, number, text))
    except Exception:
        kill_live_process(process)
        raise
    finally:
        process.stdout.close()
        code = process.wait()
    if errors or (code not in (0, 1) and not truncated):
        raise ValueError("\n".join(errors) or "ripgrep exited with status {0}".format(code))
    return results, lines, truncated


class LiveSearch:
    def __init__(self, window, directory, executable):
        self.window, self.directory, self.executable = window, directory, executable
        self.origin = window.active_view()
        self.positions = [(view, list(view.sel()), view.viewport_position()) for view in window.views()]
        self.pattern, self.filters, self.query = None, "", ""
        self.results, self.filtered = [], []
        self.lines, self.displayed = {}, []
        self.before, self.after = 0, 0
        self.index, self.generation = 0, 0
        self.process, self.preview = None, None
        self.searching, self.truncated, self.closed = False, False, False
        self.error, self.filter_error = "", ""
        self.lock = threading.Lock()

    def open(self, query):
        self.output, self.input = self.window.create_io_panel(
            LIVE_PANEL, self.submit, unlisted=True
        )
        self.input.settings().set("find_results_live_input", True)
        self.output.settings().set("word_wrap", False)
        self.output.settings().set("gutter", False)
        self.output.set_read_only(True)
        self.window.run_command("show_panel", {"panel": "output." + LIVE_PANEL})
        self.window.focus_view(self.input)
        self.input.run_command("select_all")
        self.input.run_command("insert", {"characters": query})
        self.change(query)

    def stop_process(self):
        with self.lock:
            process, self.process = self.process, None
        kill_live_process(process)

    def change(self, query):
        if self.closed:
            return
        self.query = query
        pattern, self.filters = split_live_query(query)
        if pattern != self.pattern:
            self.pattern = pattern
            self.generation += 1
            generation = self.generation
            self.stop_process()
            self.results = []
            self.lines = {}
            self.error, self.truncated = "", False
            self.searching = bool(pattern)
            if pattern:
                sublime.set_timeout(lambda: self.start_search(pattern, generation), 200)
        self.refilter()

    def start_search(self, pattern, generation):
        if self.closed or self.generation != generation:
            return
        worker = threading.Thread(target=self.search, args=(pattern, generation))
        worker.daemon = True
        worker.start()

    def search(self, pattern, generation):
        results, lines, truncated, error = [], {}, False, ""
        before, after = 0, 0
        process = None
        try:
            args, before, after = live_search_args(pattern)
            # Python 3.3 lacks CREATE_NO_WINDOW; fall back to the Win32 flag value.
            process = subprocess.Popen(
                [self.executable, "--json", "--no-config", "--color", "never",
                 "--smart-case"] + args + ["--", "."],
                cwd=self.directory, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0,
            )
            with self.lock:
                if self.closed or generation != self.generation:
                    kill_live_process(process)
                else:
                    self.process = process
            results, lines, truncated = read_live_results(process, self.directory)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as failure:
            error = str(failure)
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None

        def finish():
            if self.closed or generation != self.generation:
                return
            self.results, self.truncated, self.error = results, truncated, error
            self.lines, self.before, self.after = lines, before, after
            self.searching = False
            self.refilter()

        sublime.set_timeout(finish)

    def refilter(self):
        self.filter_error = ""
        try:
            self.filtered = filter_live_results(self.results, self.filters, self.directory)
        except ValueError as error:
            self.filtered, self.filter_error = [], str(error)
        self.displayed = live_context_results(self.filtered, self.lines, self.before, self.after)
        self.index = 0
        self.render()
        self.show_preview()

    def render(self):
        if self.error or self.filter_error:
            status = self.error or self.filter_error
        elif self.searching:
            status = "Searching..."
        elif self.truncated:
            status = "Search exceeds {0} result lines; narrow #pattern before exporting.".format(MAX_LIVE_RESULTS)
        elif not self.pattern:
            status = "Type #pattern#filters to search."
        else:
            status = "{0} filtered / {1} matches".format(len(self.filtered), len(self.results))
        header = status + "\nUp/Down: preview | Enter or Ctrl+Enter: export all filtered results | Esc: cancel\n\n"
        matches = set((filename, number) for filename, number, _ in self.filtered)
        labels = [live_result_label(result, self.directory, result[:2] in matches)[:500]
                  for result in self.displayed]
        self.output.run_command("find_results_live_render", {"text": header + "\n".join(labels)})
        self.highlight()

    def highlight(self):
        if self.filtered:
            row = self.displayed.index(self.filtered[self.index])
            region = self.output.line(self.output.text_point(row + 3, 0))
            self.output.sel().clear()
            self.output.sel().add(region)
            self.output.show(region)

    def show_preview(self):
        if self.filtered and not self.closed:
            filename, number, _ = self.filtered[self.index]
            self.preview = self.window.open_file(
                "{0}:{1}".format(filename, number), sublime.ENCODED_POSITION | sublime.TRANSIENT
            )
            self.window.focus_view(self.input)

    def move(self, step):
        if self.filtered:
            self.index = (self.index + step) % len(self.filtered)
            self.highlight()
            self.show_preview()

    def export(self):
        query = self.input.substr(sublime.Region(0, self.input.size()))
        if query != self.query:
            self.change(query)
        if self.searching or self.error or self.filter_error or not self.filtered:
            sublime.status_message("Wait for a successful search with matching results before exporting.")
            return
        # shortcut: large searches are capped; add streaming export if full scans become necessary.
        if self.truncated:
            sublime.error_message("Narrow #pattern before exporting: this search was truncated.")
            return
        text = "\n".join("{0}:{1}:{2}".format(*result) for result in self.displayed)
        matches = set((filename, number) for filename, number, _ in self.filtered)
        view = GrepResultsToFindResultsCommand(self.window).import_results(text, self.directory, matches)
        if view is not None:
            self.close(restore=False)
            self.window.focus_view(view)

    def submit(self, text):
        # Sublime clears the input before invoking the I/O panel callback.
        self.input.run_command("select_all")
        self.input.run_command("insert", {"characters": text})
        self.export()

    def close(self, restore=True):
        if self.closed:
            return
        self.closed = True
        self.generation += 1
        self.stop_process()
        if live_searches.get(self.window.id()) is self:
            live_searches.pop(self.window.id())
        if self.window.active_panel() == "output." + LIVE_PANEL:
            self.window.run_command("hide_panel")
        if self.preview and self.preview.is_valid() and not self.preview.is_dirty():
            sheet = self.preview.sheet()
            if sheet and sheet.is_transient():
                sheet.close()
        for view, selections, viewport in self.positions:
            if view.is_valid():
                view.sel().clear()
                view.sel().add_all(selections)
                view.set_viewport_position(viewport, animate=False)
        if restore and self.origin and self.origin.is_valid():
            self.window.focus_view(self.origin)


class FindResultsLiveSearchCommand(sublime_plugin.WindowCommand):
    def run(self, directory=None, query="#"):
        if not hasattr(self.window, "create_io_panel"):
            sublime.error_message("Live search requires a Sublime Text 4 build with I/O panels.")
            return
        if directory is None:
            folders = self.window.folders()
            if len(folders) != 1:
                view = self.window.active_view()
                initial = folders[0] if folders else os.path.dirname(view.file_name() or "") if view else ""
                self.window.show_input_panel(
                    "Search directory", initial,
                    lambda path: self.run(directory=path, query=query), None, None
                )
                return
            directory = folders[0]
        directory = os.path.realpath(os.path.expanduser(directory))
        if not os.path.isdir(directory):
            sublime.error_message("Search directory does not exist: " + directory)
            return
        executable = shutil.which("rg")
        if executable is None and sys.platform == "darwin":
            executable = next((path for path in ("/opt/homebrew/bin/rg", "/usr/local/bin/rg")
                               if os.path.isfile(path) and os.access(path, os.X_OK)), None)
        if executable is None:
            sublime.error_message("Install ripgrep (rg) and make it available on PATH.")
            return
        previous = live_searches.get(self.window.id())
        if previous:
            previous.close()
        session = LiveSearch(self.window, directory, executable)
        live_searches[self.window.id()] = session
        session.open(query)


class FindResultsLiveRenderCommand(sublime_plugin.TextCommand):
    def run(self, edit, text):
        self.view.set_read_only(False)
        try:
            self.view.replace(edit, sublime.Region(0, self.view.size()), text)
        finally:
            self.view.set_read_only(True)


class FindResultsLiveMoveCommand(sublime_plugin.WindowCommand):
    def run(self, step):
        session = live_searches.get(self.window.id())
        if session:
            session.move(step)


class FindResultsLiveExportCommand(sublime_plugin.WindowCommand):
    def run(self):
        session = live_searches.get(self.window.id())
        if session:
            session.export()


class FindResultsLiveCancelCommand(sublime_plugin.WindowCommand):
    def run(self):
        session = live_searches.get(self.window.id())
        if session:
            session.close()


def plugin_unloaded():
    for session in list(live_searches.values()):
        session.close(restore=False)


class Save2Command(sublime_plugin.WindowCommand):
    def run(self):
        view = self.window.active_view()
        if is_find_results(view):
            view.run_command("find_results_apply_changes")
        elif view is not None:
            view.run_command("save")


class FindResultsApplyChangesEventListener(sublime_plugin.EventListener):
    def on_modified(self, view):
        if view.settings().get("find_results_live_input") and view.window():
            session = live_searches.get(view.window().id())
            if session and view == session.input:
                text = view.substr(sublime.Region(0, view.size()))
                if text:
                    session.change(text)
                else:
                    sublime.set_timeout(
                        lambda: session.change("") if not session.closed and view.size() == 0 else None
                    )

    def on_window_command(self, window, command, args):
        session = live_searches.get(window.id())
        if session and (
            command == "hide_panel"
            or (command == "show_panel" and (args or {}).get("panel") != "output." + LIVE_PANEL)
        ):
            session.close(restore=False)

    def on_pre_close_window(self, window):
        session = live_searches.get(window.id())
        if session:
            session.close(restore=False)

    def on_new(self, view):
        sublime.set_timeout(lambda: self.on_new_ugly(view), 0)

    def on_new_ugly(self, view):
        if view.name() == "Find Results":
            view.settings().set("result_file_regex", "")
            view.settings().set("result_line_regex", "")


class FindResultsApplyChangesCommand(sublime_plugin.TextCommand):
    def run(self, edit):

        if is_find_results(self.view):
            v = self.view

            # avoid corruption

            if v.settings().get("FindResultsApplyChanges-possible-corruption", False):
                sublime.message_dialog(
                    "Committing twice when new newlines has been inserted will corrupt the file"
                )
                return

                # set 'Find results' regions

            if debug:
                draw = sublime.DRAW_OUTLINED
            else:
                draw = sublime.HIDDEN
            region_lines = v.find_all(r"^ +([0-9]+)(\: |  )")
            v.erase_regions("FindResultsApplyChanges-lines")
            v.add_regions(
                "FindResultsApplyChanges-lines",
                region_lines,
                "entity.name.function",
                "",
                draw,
            )

            region_files = v.find_all(r"^\n[^\n]+\:\n")
            v.erase_regions("FindResultsApplyChanges-files")
            v.add_regions(
                "FindResultsApplyChanges-files",
                region_files,
                "entity.class.name",
                "",
                draw,
            )

            # get 'Find Results' regions

            region_files = v.get_regions("FindResultsApplyChanges-files")
            region_lines = v.get_regions("FindResultsApplyChanges-lines")

            changes = {}

            for file in range(len(region_files)):

                region_file = region_files[file]
                try:
                    next_region_file = region_files[file + 1]
                except:
                    next_region_file = sublime.Region(v.size(), v.size())
                file_name = re.sub(r"\:$", "", v.substr(region_file).strip())

                changes[file_name] = {}

                for line in range(len(region_lines)):

                    region_line = region_lines[line]
                    try:
                        next_region_line = region_lines[line + 1]
                    except:
                        next_region_line = sublime.Region(v.size(), v.size())

                    if (
                        region_line.a > region_file.a
                        and region_line.a < next_region_file.a
                    ):
                        line_number = (
                            int(re.sub(r"\:$", "", v.substr(region_line).strip())) - 1
                        )
                        line_content = v.substr(
                            sublime.Region(
                                region_line.b,
                                (
                                    next_region_line.a
                                    if next_region_line.a < next_region_file.a
                                    else next_region_file.a
                                )
                                - 1,
                            )
                        )
                        line_content = re.sub(
                            r"\n +\.+$", "", line_content
                        )  # remove 'dots' Ellipsis
                        changes[file_name][line_number] = line_content

                # remove footer
            if changes and changes[file_name]:
                footer_line = max(changes[file_name].keys())
                changes[file_name][footer_line] = re.sub(
                    r"\n+[0-9]+ match(?:es)? (across|in) [0-9]+ files?\s*$",
                    "",
                    changes[file_name][footer_line],
                )

                # apply changes

            originals = v.settings().get("FindResultsApplyChanges-originals")
            # shortcut: native results have no search-time snapshot; rerun after external edits.
            pending = {}
            multiline = False
            try:
                resolved = {}
                for filename, replacements in changes.items():
                    filename = os.path.realpath(os.path.expanduser(filename.strip()))
                    target = resolved.setdefault(filename, {})
                    for number, replacement in replacements.items():
                        if number in target and target[number] != replacement:
                            raise ValueError("Conflicting results for the same source line: " + filename)
                        target[number] = replacement
                for filename, replacements in resolved.items():
                    if not replacements:
                        continue
                    for window in sublime.windows():
                        for view in window.views():
                            if (
                                view.file_name() and view.is_dirty()
                                and os.path.exists(view.file_name())
                                and os.path.samefile(view.file_name(), filename)
                            ):
                                raise ValueError("Save the source file before applying results: " + filename)
                    original = read_file(filename)
                    fingerprint = hashlib.sha256(original.encode("utf8")).hexdigest()
                    if originals is not None and originals.get(filename) != fingerprint:
                        raise ValueError("Source changed since grep import; rerun grep: " + filename)
                    content = original.split("\n")
                    for number, replacement in replacements.items():
                        if number < 0 or number >= len(content):
                            raise ValueError("Result line is outside the source file: " + filename)
                        ending = "\r" if content[number].endswith("\r") else ""
                        replacement = replacement.replace(
                            "\n", "\r\n" if ending else "\n"
                        ) + ending
                        if content[number] != replacement:
                            content[number] = replacement
                            multiline = multiline or "\n" in replacement
                    updated = "\n".join(content)
                    if updated != original:
                        pending[filename] = updated
                for filename, content in pending.items():
                    self.write(filename, content)
                    if originals is not None:
                        originals[filename] = hashlib.sha256(content.encode("utf8")).hexdigest()
                    if multiline:
                        v.settings().set("FindResultsApplyChanges-possible-corruption", True)
            except (OSError, UnicodeError, ValueError) as error:
                sublime.error_message(str(error))
                return
            if originals is not None:
                v.settings().set("FindResultsApplyChanges-originals", originals)
            sublime.status_message(
                "Written modifications to {0} file(s)".format(len(pending))
            )

    def is_enabled(self):
        return is_find_results(self.view)

    def write(self, filepath, c):
        with open(filepath, "w+", encoding="utf8", newline="") as f:
            f.write(str(c))
