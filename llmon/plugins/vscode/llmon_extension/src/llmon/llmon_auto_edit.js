// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');

// Detects a completed LLMON opening tag immediately before the cursor:
//   <|open|>name<|close|>   (possibly with <|.|> and <|:|> in the name)
// The name must not swallow another structural token (<|open|>, <|open_end|>),
// or a bare <|open_end|> later in the document would be absorbed as if it
// were part of this tag's name.
const RE_M_OPENING_BEFORE_CURSOR = /<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>$/;

// Detects a closing tag at the start of trimmed text:
//   <|open_end|>name<|close|>
const RE_M_CLOSING_TAG = /^<\|open_end\|>((?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/;

// Re-entrancy guards
let isAutoClosing  = false;
let isSmartEntering = false;
let isAutoDeletingClose = false;

function getIndent(lineText) {
  const m = lineText.match(/^(\s*)/);
  return m ? m[1] : '';
}

function detectIndentUnit(doc) {
  for (let i = 0; i < doc.lineCount; i++) {
    const text = doc.lineAt(i).text;
    const m = text.match(/^(\s+)\S/);
    if (m) return m[1];
  }
  return '  ';
}

/**
 * Smart Enter: when Enter splits a line with <|open|>name<|close|> above
 * and <|open_end|>name<|close|> below, expand to a properly-indented inner line.
 */
function registerLlmonSmartEnter(context) {
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      if (isSmartEntering) return;
      const doc = event.document;
      if (doc.languageId !== 'llmon') return;

      if (event.contentChanges.length !== 1) return;
      const change = event.contentChanges[0];
      if (!/^\r?\n/.test(change.text)) return;

      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document !== doc) return;

      const newLineNum = change.range.start.line + 1;
      if (newLineNum >= doc.lineCount) return;

      const prevLine = doc.lineAt(newLineNum - 1).text;
      const curLine  = doc.lineAt(newLineNum).text;

      // Find last opening tag on previous line
      const openMatches = [...prevLine.matchAll(/<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g)];
      if (!openMatches.length) return;
      const openMatch = openMatches[openMatches.length - 1];

      const curTrimmed = curLine.trimStart();
      const closeMatch = curTrimmed.match(RE_M_CLOSING_TAG);
      if (!closeMatch) return;

      if (openMatch[1] !== closeMatch[1]) return;

      const prevIndent  = getIndent(prevLine);
      const indentUnit  = detectIndentUnit(doc);
      const innerIndent = prevIndent + indentUnit;
      const closingLine = prevIndent + curTrimmed;

      isSmartEntering = true;
      editor.edit(editBuilder => {
        const range = new vscode.Range(
          new vscode.Position(newLineNum, 0),
          new vscode.Position(newLineNum, curLine.length)
        );
        editBuilder.replace(range, innerIndent + '\n' + closingLine);
      }, { undoStopBefore: false, undoStopAfter: false }).then(() => {
        isSmartEntering = false;
        const cursorPos = new vscode.Position(newLineNum, innerIndent.length);
        editor.selection = new vscode.Selection(cursorPos, cursorPos);
      }, () => { isSmartEntering = false; });
    })
  );
}

/**
 * Auto-close: when the user finishes typing <|open|>name<|close|> (trigger: `>`),
 * automatically insert <|open_end|>name<|close|> after the cursor.
 */
function registerLlmonAutoClose(context) {
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      if (isAutoClosing) return;
      const doc = event.document;
      if (doc.languageId !== 'llmon') return;

      if (event.contentChanges.length !== 1) return;
      const change = event.contentChanges[0];
      if (change.text !== '>') return;

      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document !== doc) return;

      const insertedPos = change.range.start;
      const cursorPos = new vscode.Position(insertedPos.line, insertedPos.character + 1);

      const lineText = doc.lineAt(cursorPos.line).text.slice(0, cursorPos.character);
      const match = RE_M_OPENING_BEFORE_CURSOR.exec(lineText);
      if (!match) return;

      const rawName = match[1];

      // Build the closing tag string
      const closing = `<|open_end|>${rawName}<|close|>`;

      // Skip if a closing tag for this name already appears after the cursor
      const textAfterCursor = doc.getText().slice(doc.offsetAt(cursorPos));
      const escapedName = rawName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      if (new RegExp(`<\\|open_end\\|>${escapedName}<\\|close\\|>`).test(textAfterCursor)) return;

      isAutoClosing = true;
      editor.edit(editBuilder => {
        editBuilder.insert(cursorPos, closing);
      }, { undoStopBefore: false, undoStopAfter: false }).then(() => {
        isAutoClosing = false;
        const newPos = new vscode.Position(cursorPos.line, cursorPos.character);
        editor.selection = new vscode.Selection(newPos, newPos);
      }, () => { isAutoClosing = false; });
    })
  );
}

/**
 * Auto-delete closing tag: when an opening tag sequence is partially deleted
 * and the corresponding closing tag immediately follows (with only whitespace
 * between), remove the orphaned closing tag.
 */
function registerLlmonAutoDeleteClose(context) {
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      if (isAutoDeletingClose || isAutoClosing) return;
      const doc = event.document;
      if (doc.languageId !== 'llmon') return;

      if (event.contentChanges.length !== 1) return;
      const change = event.contentChanges[0];
      if (change.rangeLength === 0) return;

      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document !== doc) return;

      const fullText   = doc.getText();
      const startOffset = doc.offsetAt(change.range.start);

      // Look back up to 120 chars for the start of an opening tag
      const lookBehind = 120;
      const scanStart  = Math.max(0, startOffset - lookBehind);
      const before     = fullText.slice(scanStart, startOffset);
      const after      = fullText.slice(startOffset);

      // Find last <|open| fragment in before
      const lastOpenIdx = before.lastIndexOf('<|open|>');
      if (lastOpenIdx === -1) return;

      const fragment = before.slice(lastOpenIdx);

      // The fragment should look like an incomplete opening tag:
      // <|open|>name... but NOT a complete <|open|>name<|close|>
      const completeOpen = /<\|open\|>((?:[^<]|<(?!\|))+?)<\|close\|>$/.test(fragment);
      if (completeOpen) return; // Tag is still complete — don't delete

      // Extract the partial name from the fragment
      const partialMatch = fragment.match(/^<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])*)$/);
      if (!partialMatch) return;

      const partialName = partialMatch[1];

      // Look for a closing tag in `after` (with optional whitespace first)
      const escapedName = partialName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const closingPattern = new RegExp(`^([\\s\\u200B\\uFEFF\\u00A0\\u2000-\\u200F\\u2028\\u2029]*)(<\\|open_end\\|>${escapedName}<\\|close\\|>)`);
      const closeMatch = closingPattern.exec(after);
      if (!closeMatch) return;

      const closingTagText   = closeMatch[2];
      const closingTagOffset = startOffset + closeMatch[1].length;
      const closeStart = doc.positionAt(closingTagOffset);
      const closeEnd   = doc.positionAt(closingTagOffset + closingTagText.length);

      isAutoDeletingClose = true;
      editor.edit(editBuilder => {
        editBuilder.delete(new vscode.Range(closeStart, closeEnd));
      }, { undoStopBefore: false, undoStopAfter: false }).then(() => {
        isAutoDeletingClose = false;
      }, () => { isAutoDeletingClose = false; });
    })
  );
}

function registerLlmonAutoEdits(context) {
  registerLlmonSmartEnter(context);
  registerLlmonAutoClose(context);
  registerLlmonAutoDeleteClose(context);
}

module.exports = { registerLlmonAutoEdits };
