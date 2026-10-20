// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');

// Completions offered after the user has typed <|open|>
// insertText is what replaces <|open|> onward (the trigger `|` is the last char of <|open|>).
const LLMON_BUILTIN_COMPLETIONS = [
  {
    label: 'instr',
    detail: 'Instruction tag',
    documentation: 'Instruction tags specify behaviors and directives that an LLM should follow explicitly.',
    insertText: 'instr<|close|>',
  },
  {
    label: 'instr<|:|>',
    detail: 'Named instruction tag',
    documentation: 'A named instruction tag. The label after <|:|> identifies this instruction for later reference.',
    insertText: 'instr<|:|>',
  },
  {
    label: 'instruction',
    detail: 'Instruction tag (long form)',
    documentation: 'Instruction tags specify behaviors and directives that an LLM should follow explicitly.',
    insertText: 'instruction<|close|>',
  },
  {
    label: 'instruction<|:|>',
    detail: 'Named instruction tag (long form)',
    documentation: 'A named instruction tag (long form). The label after <|:|> identifies this instruction for later reference.',
    insertText: 'instruction<|:|>',
  },
  {
    label: 'data',
    detail: 'Data tag',
    documentation: 'Data tags contain raw data or values to be consumed or referenced by instructions and execution blocks.',
    insertText: 'data<|close|>',
  },
  {
    label: 'data<|:|>',
    detail: 'Named data tag',
    documentation: 'A named data tag. The label after <|:|> identifies this data block for later reference.',
    insertText: 'data<|:|>',
  },
  {
    label: 'exec',
    detail: 'Execution tag',
    documentation: 'Execution tags mark blocks that are intended to be executed or run — they define actions, calls, or operations.',
    insertText: 'exec<|close|>',
  },
  {
    label: 'exec<|:|>',
    detail: 'Named execution tag',
    documentation: 'A named execution tag. The label after <|:|> identifies this execution block for later reference.',
    insertText: 'exec<|:|>',
  },
  {
    label: 'execution',
    detail: 'Execution tag (long form)',
    documentation: 'Execution tags mark blocks that are intended to be executed or run — they define actions, calls, or operations.',
    insertText: 'execution<|close|>',
  },
  {
    label: 'execution<|:|>',
    detail: 'Named execution tag (long form)',
    documentation: 'A named execution tag (long form). The label after <|:|> identifies this execution block for later reference.',
    insertText: 'execution<|:|>',
  },
];

const llmonCompletionProvider = {
  provideCompletionItems(document, position) {
    if (document.languageId !== 'llmon') return null;

    const lineText         = document.lineAt(position.line).text;
    const textBeforeCursor = lineText.slice(0, position.character);

    // Trigger: the user has just completed typing <|open|>
    if (!textBeforeCursor.endsWith('<|open|>')) return null;

    return LLMON_BUILTIN_COMPLETIONS.map(entry => {
      const item = new vscode.CompletionItem(entry.label, vscode.CompletionItemKind.Keyword);
      item.detail        = entry.detail;
      item.documentation = new vscode.MarkdownString(entry.documentation);
      item.insertText    = entry.insertText;
      // Replace the '<|open|>' that was just typed
      const replaceStart = new vscode.Position(position.line, position.character - '<|open|>'.length);
      item.range = new vscode.Range(replaceStart, position);
      return item;
    });
  }
};

module.exports = { llmonCompletionProvider };
