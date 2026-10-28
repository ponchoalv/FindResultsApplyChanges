# coding=utf8
import sublime
import sublime_plugin
import re
import os
import hashlib

debug = False


def is_find_results(view):
    return view is not None and (
        view.name() == "Find Results"
        or "Find Results." in (view.settings().get("syntax") or "")
    )


def read_file(filepath):
    with open(filepath, "r", newline="", encoding="utf8") as source:
        return source.read()


def grep_to_find_results(text, base_dir):
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
    for filename in sorted(changes):
        blocks.append("\n" + filename + ":\n")
        for number in sorted(changes[filename]):
            blocks.append("  {0}: {1}\n".format(number, changes[filename][number]))
    blocks.append(
        "\n{0} matches across {1} files\n".format(
            sum(len(lines) for lines in changes.values()), len(changes)
        )
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

    def import_results(self, text, base_dir):
        try:
            results, originals = grep_to_find_results(text, base_dir)
        except (OSError, UnicodeError, ValueError) as error:
            sublime.error_message(str(error))
            return
        view = self.window.new_file()
        view.set_name("Find Results")
        view.set_syntax_file("Packages/Default/Find Results.hidden-tmLanguage")
        view.set_scratch(True)
        view.settings().set("FindResultsApplyChanges-originals", originals)
        view.run_command("append", {"characters": results})


class Save2Command(sublime_plugin.WindowCommand):
    def run(self):
        view = self.window.active_view()
        if is_find_results(view):
            view.run_command("find_results_apply_changes")
        elif view is not None:
            view.run_command("save")


class FindResultsApplyChangesEventListener(sublime_plugin.EventListener):
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
