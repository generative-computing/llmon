# Repository Map

Overview of the folders and files in this VS Code extension for Shorthand / LLMON language support

## Root

- **package.json** — Extension manifest. Declares the VS Code engine requirement, contributed commands (convert between Shorthand/LLMON/JSON), the `.short`/`.llmon` language definitions, syntax grammars, color theme, semantic token types, and settings for locating the converter binary
- **short-language-configuration.json** — Editor language configuration for Shorthand: bracket pairs (`\object\`, `\list\`, `\item\` and their closing forms), auto-closing quote pairs, and indentation rules based on tag presence
- **llmon-language-configuration.json** — Same kind of configuration but for LLMON, using the `<|open|>` / `<|open_end|>` tokens to drive indent increase/decrease
- **CHANGELOG.md** — Version history of the extension
- **README.md** — Project overview/usage documentation
- **LICENSE.md** — License terms
- **.vscode/launch.json** — Debug launch configuration for running/testing the extension in a VS Code Extension Development Host

## src/ — Extension source code

Root of the source tree; contains the activation entry point plus subfolders split by language variant

- **src/extension.js** — Main activation entry point. Registers all providers (semantic tokens, definitions, completions, hover), diagnostics collections, formatting providers, auto-edit behaviors, tag-pair highlighting, and the conversion commands for both Shorthand and LLMON

### src/converters/

Logic for converting between the different notations via an external CLI

- **convert_command.js** — Implements the "Convert to View" command. Spawns Python converter CLI binaries (`short-to-llmon`, `llmon-to-short`, `llmon-to-json`, `json-to-llmon`), resolving the binary via configured path, system PATH, or login shell. Also handles synchronized scrolling between source and converted-result editors, and surfaces conversion errors

### src/short/ — Human-readable Shorthand language features

Provider implementations for `.short` files (syntax using `\tag\` / `/tag/` / `\tag/`)

- **short_auto_edit.js** — Auto-editing behaviors: smart-Enter (expands an inner line when Enter splits an open/close tag pair), auto-close (inserts the matching closing tag), and auto-delete-close (removes an orphaned closing tag if its opening tag is edited away)
- **short_completion_provider.js** — Autocomplete for builtin tag names (`instr`, `data`, `exec`, `instruction`, `execution`) and their prefixed forms (e.g. `instr:`) after typing `\`
- **short_definition_provider.js** — Go-to-definition support: resolves bare-word and prefixed references to the location of their `\name\` opening tag
- **short_diagnostics.js** — Diagnostic rules: unbalanced tags (unclosed opens / orphaned closes), empty-colon tags (`tag:` with no label), and indentation validation
- **short_formatter.js** — Document/range formatting: splits inline tags onto their own lines with correct indentation, plus a "compact" command that collapses whitespace between tokens
- **short_hover_provider.js** — Hover tooltips describing builtin tags, label metadata, and clickable "go to definition" links for references
- **short_semantic_tokens.js** — Semantic token classification: assigns user-defined tags to one of 5 color buckets, classifies builtin tags (`object`, `item`, `list`, `int`, `float`, `bool`, `none`, `input`, `output`), and flags references that lack a definition
- **short_tag_pair_highlight.js** — Highlights the matched open/close tag pair around the cursor and draws a vertical scope-guide line spanning the pair; resets on cursor move or edit. Also owns the two decoration types that the LLMON highlighter reuses

### src/llmon/ — Machine-readable LLMON language features

Mirrors the `src/short/` providers, adapted for the `.llmon` token syntax (`<|open|>`, `<|open_end|>`, `<|close|>`, `<|self_close|>`, `<|.|>`, `<|:|>`)

- **llmon_auto_edit.js** — Same auto-edit behaviors as the Shorthand version (smart-Enter, auto-close, auto-delete-close), driven by LLMON token syntax
- **llmon_completion_provider.js** — Completion for builtin tag names after typing `<|open|>`, including variants like `instr<|close|>` and `instr<|:|>`
- **llmon_definition_provider.js** — Go-to-definition for LLMON references, normalizing `<|.|>`/`<|:|>` tokens before locating the opening tag
- **llmon_diagnostics.js** — Diagnostics equivalent to the Shorthand version, plus handling of bare `<|open_end|>` closes and detection of malformed nested delimiters (e.g. `<|open|><|open|>`)
- **llmon_formatter.js** — Document/range formatter and "compact" command for LLMON, expanding inline tags and computing indentation while preserving tokens
- **llmon_hover_provider.js** — Hover tooltips for LLMON tags/references, with logic to detect tag names inside `<|open|>name<|close|>` structures
- **llmon_semantic_tokens.js** — Semantic tokens for LLMON; handles bare closing tags that match the innermost open tag, and tokenized references like `instr<|:|>label`
- **llmon_tag_pair_highlight.js** — Tag-pair highlighting for LLMON, built on the shared scope-guide logic in `src/shared/tag_matching.js` and the decoration types owned by `src/short/short_tag_pair_highlight.js`

### src/shared/

Language-agnostic helpers reused by both Shorthand and LLMON

- **tag_matching.js** — Shared algorithms: stack-based tag matching, indentation detection (via GCD of indentation amounts), and indent-depth resolution, used by both languages' diagnostics, formatters, and tag-pair highlighters

## syntaxes/

TextMate grammars used for base syntax highlighting

- **short.tmLanguage.json** — Grammar for Shorthand: scope names/patterns for builtin tags (`instr`, `data`, `exec`, `object`, `item`, `list`, `int`, `float`, `bool`, `none`, `input`, `output`), user tags, primitives, and folding markers
- **llmon.tmLanguage.json** — Grammar for LLMON: patterns for the structural tokens (`<|open|>`, `<|close|>`, `<|self_close|>`, `<|open_end|>`), tag variants, bare-close tags, and list separators

## themes/

Color theme assets and extension branding.

- **llmon-color-theme.json** — Dark color theme defining semantic token colors for all Shorthand/LLMON token types (5 user-tag buckets, prefixed refs, builtin tags, references) plus general editor UI colors
- **dark_plus_base.json** — Base dark theme color values used as a foundation for the LLMON theme
- **short.svg** — Shorthand logo/icon (vector)
- **llmon.svg** — LLMON logo/icon (vector)
- **llmon_icon.png** — LLMON icon (raster), used as extension/marketplace icon
