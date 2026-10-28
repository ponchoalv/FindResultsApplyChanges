# Find Results Apply Changes

## Description

Applies any change you made to a "Find Results" buffer back to the files when
you press `ctrl/cmd+s`.

This fork fixes Sublime Text 4's `~/...` result paths and adds an importer for
grep and ripgrep output. Both use the same editable Find Results buffer.

## Installation

Open `Preferences > Browse Packages...` and clone this fork into a directory
named `FindResultsApplyChanges` inside the opened Packages directory. Restart
Sublime Text. Use this fork instead of a second, differently named copy of the
upstream plugin.

## Native search results

Run Find in Files (`Ctrl+Shift+F`, or `Cmd+Shift+F` on macOS) with results shown
in a separate view. Edit the result text using normal editing or multiple
cursors, then press `Ctrl/Cmd+S` or run **Find Results: Apply Changes** from the
Command Palette. Source files are written directly; there is no extra Save All
step.

## Grep and ripgrep results

Produce uncoloured `file:line:text` results, for example:

```sh
rg --no-heading --color never -nH 'pattern' .
grep -rnH 'pattern' src
```

Open or paste that output into a Sublime Text buffer. Run **Find Results:
Import Grep/Ripgrep Results** from the Command Palette and enter the directory
where the search command ran. A new Find Results buffer opens, ready for
multiple-cursor editing and `Ctrl/Cmd+S`.

Relative paths, absolute paths, `~/...` paths, filenames containing spaces,
and duplicate results are supported. The importer verifies that each result
still matches its source line, and applying edits refuses to overwrite files
changed since import or source buffers with unsaved edits.

## Limits

- UTF-8 source files only. Grep input must contain full lines with line numbers,
  without colours, headings, context lines, or column numbers.
- Edit the result text; keep filename headers and line-number prefixes intact.
- After inserting newlines and applying once, rerun the search before applying
  again. A guard blocks reapplying the old line numbers.
- Native results have no search-time snapshot. Rerun native searches after
  source files change outside the results buffer.
- Applying writes directly to disk, file by file. Validation happens before
  writing, but an I/O failure can leave some files already updated.

## Tests

Run `python3 -m unittest discover -s tests -v`. The tests use a minimal Sublime
API stand-in and temporary source files, including round trips with installed
`grep` and `rg` executables. No third-party Python dependencies are required.

## Source-code

Fork: https://github.com/ponchoalv/FindResultsApplyChanges

Upstream: https://github.com/titoBouzout/FindResultsApplyChanges

## License

"None are so hopelessly enslaved as those who falsely believe they are free."
Johann Wolfgang von Goethe

Copyeverything (C) 2014 Tito Bouzout <tito.bouzout@gmail.com>

This license apply to all the files inside this program unless noted different
for some files or portions of code inside these files.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation. http://www.gnu.org/licenses/gpl.html

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see http://www.gnu.org/licenses/gpl.html
