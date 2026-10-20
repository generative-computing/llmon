# Change Log

v0.0.10
- Added the Apache-2.0 licence text to the extension package and declared the licence in
  `package.json`. Earlier builds shipped an empty licence file.
- Retained Microsoft's MIT notice in the licence file, because the base theme is derived from
  the Visual Studio Code Dark+ theme.

v0.0.9
- Renamed both supported formats. Terminology below this entry is historical and uses the old names.
    - The human-readable `\tag\` / `/tag/` syntax is now **Shorthand**: extension `.short`, language id `short`
    - The `<|open|>` token syntax is now **LLMON**: extension `.llmon`, language id `llmon` (formerly MLLMON / `.mllmon` / `machine_llmon`)
- **Breaking — existing files must be renamed.** A `.llmon` file written in the old shorthand
  syntax keeps its extension but is now parsed by the LLMON grammar, so it will show no
  highlighting and errors throughout. Rename shorthand `.llmon` files to `.short`, and old
  `.mllmon` files to `.llmon`.
- Converter commands now invoke `short-to-llmon`, `llmon-to-short`, `llmon-to-json` and
  `json-to-llmon`, replacing the former `llmon-to-mllmon`, `mllmon-to-llmon`, `mllmon-to-json`
  and `json-to-mllmon`. Reinstall the Python package (`pip install -e .`) so the new
  console scripts are on your PATH.
- Menu items renamed accordingly: Shorthand files offer "Convert to LLMON", LLMON files offer
  "Convert to Shorthand" and "Convert to JSON", and JSON files offer "Convert to LLMON".

v0.0.8
- <|open_end|> MLLMON tags can now close any opened tag

v0.0.7
- Added support for new JSON-to-MLLMON flags
    - No composite tag (-noct)
    - No open end tag (-noot)
    - No type tags (-ntt)
- Consolidations to codebase

v0.0.6
- Fixed 'Compact' function behvior so it properly works on edge cases

v0.0.5
- Added file conversion and side by side display with synchronized scrolling
- Added 'Compact' function
    - Removes all whitespace in the file (apart from in tag names and content)

v0.0.4
- Fixed LLMON tags not allowing spaces in their names

v0.0.3
- 'Format Document' function now properly supports compact LLMON and MLLMON files (all tags and content on one line)
- Distinct, consistent colors and IntelliSense hover support added for following tag types
    - object, item, list, int, float, bool, none, input, output
- Minor fixes

v0.0.2
- LLMON Theme no longer overrides default colors/highlighting for other languages
- Added scope guide highlighting
- Fixed references not being properly highlighted
- 'Format Document' function now works correctly
- LLMON and MLLMON files now have unique icons to differentiate from other languages and one another