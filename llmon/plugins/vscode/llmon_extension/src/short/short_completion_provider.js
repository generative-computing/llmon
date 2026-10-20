// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');

const BUILTIN_TAG_COMPLETIONS = [
  {
    label: 'instr',
    detail: 'Instruction tag',
    documentation: 'Instruction tags specify behaviors and directives that an LLM should follow explicitly.',
    insertText: 'instr\\',
  },
  {
    label: 'instr:',
    detail: 'Named instruction tag',
    documentation: 'A named instruction tag (e.g. \\instr:label\\). The label identifies this instruction for later reference.',
    insertText: 'instr:',
  },
  {
    label: 'instruction',
    detail: 'Instruction tag (long form)',
    documentation: 'Instruction tags specify behaviors and directives that an LLM should follow explicitly.',
    insertText: 'instruction\\',
  },
  {
    label: 'instruction:',
    detail: 'Named instruction tag (long form)',
    documentation: 'A named instruction tag (e.g. \\instruction:label\\). The label identifies this instruction for later reference.',
    insertText: 'instruction:',
  },
  {
    label: 'data',
    detail: 'Data tag',
    documentation: 'Data tags contain raw data or values to be consumed or referenced by instructions and execution blocks.',
    insertText: 'data\\',
  },
  {
    label: 'data:',
    detail: 'Named data tag',
    documentation: 'A named data tag (e.g. \\data:label\\). The label identifies this data block for later reference.',
    insertText: 'data:',
  },
  {
    label: 'exec',
    detail: 'Execution tag',
    documentation: 'Execution tags mark blocks that are intended to be executed or run — they define actions, calls, or operations.',
    insertText: 'exec\\',
  },
  {
    label: 'exec:',
    detail: 'Named execution tag',
    documentation: 'A named execution tag (e.g. \\exec:label\\). The label identifies this execution block for later reference.',
    insertText: 'exec:',
  },
  {
    label: 'execution',
    detail: 'Execution tag (long form)',
    documentation: 'Execution tags mark blocks that are intended to be executed or run — they define actions, calls, or operations.',
    insertText: 'execution\\',
  },
  {
    label: 'execution:',
    detail: 'Named execution tag (long form)',
    documentation: 'A named execution tag (e.g. \\execution:label\\). The label identifies this execution block for later reference.',
    insertText: 'execution:',
  },
];

const completionProvider = {
  provideCompletionItems(document, position) {
    if (document.languageId !== 'short') return null;

    const lineText = document.lineAt(position.line).text;
    const textBeforeCursor = lineText.slice(0, position.character);
    if (!textBeforeCursor.endsWith('\\')) return null;

    return BUILTIN_TAG_COMPLETIONS.map(entry => {
      const item = new vscode.CompletionItem(entry.label, vscode.CompletionItemKind.Keyword);
      item.detail = entry.detail;
      item.documentation = new vscode.MarkdownString(entry.documentation);
      item.insertText = entry.insertText;
      const replaceStart = new vscode.Position(position.line, position.character - 1);
      item.range = new vscode.Range(replaceStart, position);
      return item;
    });
  }
};

module.exports = { completionProvider };
