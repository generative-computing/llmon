This directory contains code related to plugins for IDEs for LLMON and Shorthand.

Here are the relevant directories:
- `vscode/`: a VSCode extension for LLMON and Shorthand. See vscode/README.md for more details
- `emacs/`: emacs elisp files for LLMON and Shorthand.

## Emacs

Each file provides one major mode, which Emacs selects from the file extension:

- `emacs/llmon-mode.el` provides `llmon-mode` for `.llmon` files, which use the LLMON syntax with special tokens such as `<|open|>`.
- `emacs/short-mode.el` provides `short-mode` for `.short` files, which use the Shorthand syntax with `\tag\ ... /tag/`.

Files with any other extension are not claimed by either mode. Both modes provide syntax highlighting, indentation and commands for moving between matching tags. Neither mode converts between formats.

Known limitation: in `llmon-mode`, tag matching and navigation pair a closing tag with its opening tag by name. They work only when closers carry the tag name (`<|open_end|>NAME<|close|>`), and on the bare `<|open_end|>` closer they report "No matching close tag". Highlighting is not affected. `short-mode` is not affected by this limitation.
