# Find Results Apply Changes

## Description

Applies any change you made to a "Find Results" buffer back to the files when
you press `ctrl/cmd+s`.

This fork fixes Sublime Text 4's `~/...` result paths and adds an importer for
grep and ripgrep output, plus live search with previews and `#pattern#filter`
narrowing. All three use the same editable Find Results buffer.

## Installation

Open `Preferences > Browse Packages...` and clone this fork into a directory
named `FindResultsApplyChanges` inside the opened Packages directory. Restart
Sublime Text. Use this fork instead of a second, differently named copy of the
upstream plugin.

## Live search, preview, and export

Install `ripgrep` (`rg`) and run **Find Results: Live Ripgrep** from the Command
Palette. The search uses the project folder; if the window has several folders
or none, it asks for a directory. Live search requires a Sublime Text 4 build
with I/O panels; native result editing and grep import still work without them.

Type `#pattern#filters`, for example:

```text
#error#payment !tests
#user.*name#src "first name"
#case '.*': -C2#/day20/
#error -A 3 -B 1 -g '*.py'#payment !tests
#-i -C2 -- error#src
```

The part before the second `#` contains the ripgrep regular expression and
options. Put options after the pattern, or put them first and use `--` before
the pattern. Regex quotes and backslashes are preserved. Escape a literal
leading dash as `\-`, or put the pattern after `--`.

`-C2` shows two lines of context on both sides; `-A` and `-B` select after/before
context independently. Attached, separated, and long forms work (`-C2`,
`-C 2`, `--context=2`). Context stays with each remaining hit after filtering
and is included in the exported buffer, where it can also be edited.

Search and file-selection options include `-i`, `-s`, `-S`, `-F`, `-w`, `-x`,
`-v`, `-P`, `-g`, `--iglob`, `-t`, `-T`, `--type-add`, `--type-clear`, `--hidden`,
`--no-ignore` and its variants, `-u`, `-L`, `--ignore-file`, `--max-depth`,
`--max-filesize`, `-m`, `-j`, `--engine`, `--sort`, `--sortr`, `-e`, and `-f`.
Short flags can be combined, and option values use shell-style quoting.
Output modes such as `--count`, `--files`, `--replace`, and `--only-matching`,
plus multiline, non-UTF-8 encodings, compressed files, and preprocessors are
unsupported in editable live search; incompatible options show an error.

Changing the first section starts a new search after a short typing delay
and cancels the previous one.
The remaining part filters the existing results immediately, without rerunning
ripgrep. Space-separated terms must all appear in the path or line text;
`!term` excludes a term, and quotes group phrases. Both stages use smart case:
lowercase input ignores case, uppercase input makes matching case-sensitive.
These filters are literal terms, not Emacs completion styles or fuzzy matching.
Flags only affect the first section. A query without a leading `#` is treated
entirely as the first section, including any options.

- `Up` / `Down`: preview the matching source line while keeping input focus.
- `Enter` / `Ctrl+Enter`: export **all filtered results** into an editable Find
  Results tab. **Find Results: Export Live Results** does the same.
- `Esc`: cancel the search and restore the original editor selection and scroll.
- In the exported tab, use multiple cursors and `Ctrl/Cmd+S` to apply your edits.

Only ripgrep is required; there is no `fzf` dependency. Live searches respect
ripgrep's default ignore rules. Searches above 10,000 result lines (including
context) are marked as truncated, and export is blocked until the first section
is narrowed.
Displayed lines are shortened to 500 characters; exported lines are complete.

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
