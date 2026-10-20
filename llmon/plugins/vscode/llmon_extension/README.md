# LLMON Extension README

Visual Studio Code extension implementing LLMON (LLM Object Notation) and Shorthand support.

Two file types are supported:

* **`.short`** — Shorthand, the human-readable form using `\tag\` / `/tag/` / `\tag/`
* **`.llmon`** — LLMON, the machine form using `<|open|>`, `<|open_end|>`, `<|close|>`, `<|self_close|>`

> **Migrating from v0.0.8 or earlier.** These formats were previously named "LLMON" (`.llmon`)
> and "Machine LLMON" (`.mllmon`). The `.llmon` extension now means the *machine* form, so a
> `.llmon` file written in the old shorthand syntax will show no highlighting and errors
> throughout until you rename it to `.short`. Old `.mllmon` files should be renamed to `.llmon`.

## Features

* Syntax highlighting for tags
    * Distinct, consistent colors and IntelliSense hover support for following tag types
        * instruction, data, execution, object, item, list, int, float, bool, none, input, output
    * Rotating colors for user tags
* Reference highlighting
    * Matches the color of the tags it's referencing and only highlights if the tag being referenced actually exists
* Automatic closing tag creation/deletion
    * When you create an opening tag, it will automatically create a closing tag for you
        * e.g. typing \tag\ will create /tag/ automatically
    * When you delete an opening tag it will delete the associated closing tag if there was only whitespace within
* Automatic indentation/formatting
    * New lines within tags will indent to the correct position
    * The 'Format Document' function (accessed through right clicking in editor) can properly reformat your entire file correctly
    * The 'Compact' function (also accessed through right clicking in editor) will remove all whitespace in a file (apart from in tag names and content), resulting in a one line file
* Active scope highlighting
    * Highlighting of complimentary opening/closing tags and scope guide for easy visual identification
* 'Go to Definiton'
    * Allows you to see where in the file a tag being referenced is created
* IntelliSense hover information
    * Hovering over (non-user) tags will show information on their purpose, associated labels, and type
* Error detection and warnings
    * Invalid/unclosed/orphaned tags marked as errors
    * Mismatched indentation marked as a warning 
* File conversion and display
    * Files open in the editor can be converted to other valid types
        * Shorthand --> LLMON, LLMON --> Shorthand, LLMON --> JSON, JSON --> LLMON
    * Converted files are opened in split screen next to the main editor with synchronized scrolling

## Requirements

Node.js, Python 3.10+, LLMON package

## Extension Settings

'LLMON Theme' is required to be selected under >Preferences: Color Theme for features to work

## Installation
A packaged VSIX is provided in the /vscode directory. Once you have downloaded the VSIX, open the extensions tab in VSCode, click the three dots in the top right, select 'Install from VSIX...', and locate the file. Once installed, if the listed features don't work immediately, ensure 'LLMON Theme' is selected under >Preferences: Color Theme

If you would like to package from the source code, please run 'vsce package --allow-missing-repository' from the root (you must have Node.js and install the VS Code Extension Manager through 'npm install -g @vscode/vsce')