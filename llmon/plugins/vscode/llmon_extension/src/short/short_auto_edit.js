// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { RE_PREFIXED } = require('./short_semantic_tokens');

// Tag name: starts with non-space word char, optionally interior chars (incl. spaces) ending on non-space word char
const TAG_NAME = '[A-Za-z0-9_](?:[A-Za-z0-9._: ]*[A-Za-z0-9._:])?';

// Matches a completed opening tag immediately before a position: \tagname\
const RE_OPENING_TAG_BEFORE_CURSOR = new RegExp(`\\\\(${TAG_NAME})\\\\$`);

// Matches a closing tag at the start of trimmed text: /tagname/
const RE_CLOSING_TAG = new RegExp(`^\\/(${TAG_NAME})\\/`);

// Guards against re-entrancy for each auto-edit operation.
let isAutoClosing = false;
let isSmartEntering = false;
let isAutoDeletingClose = false;

/** Return the leading whitespace string of a line. */
function getIndent(lineText) {
  const m = lineText.match(/^(\s*)/);
  return m ? m[1] : '';
}

/**
 * Return the indentation unit used in the document (first indented line's
 * leading whitespace), defaulting to four spaces.
 */
function detectIndentUnit(doc) {
  for (let i = 0; i < doc.lineCount; i++) {
    const text = doc.lineAt(i).text;
    const m = text.match(/^(\s+)\S/);
    if (m) return m[1];
  }
  return '    ';
}

/**
 * Register smart-Enter: when Enter splits a line with \tag\ above and /tag/ below,
 * expand to a properly-indented inner line.
 */
function registerSmartEnter(context) {
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      if (isSmartEntering) return;
      const doc = event.document;
      if (doc.languageId !== 'short') return;

      if (event.contentChanges.length !== 1) return;
      const change = event.contentChanges[0];
      if (!/^\r?\n/.test(change.text)) return;

      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document !== doc) return;

      const newLineNum = change.range.start.line + 1;
      if (newLineNum >= doc.lineCount) return;

      const prevLine = doc.lineAt(newLineNum - 1).text;
      const curLine  = doc.lineAt(newLineNum).text;

      const openMatches = [...prevLine.matchAll(new RegExp(`\\\\(${TAG_NAME})\\\\(?!\\/)`, 'g'))];
      if (!openMatches.length) return;
      const openMatch = openMatches[openMatches.length - 1];

      const curTrimmed = curLine.trimStart();
      const closeMatch = curTrimmed.match(RE_CLOSING_TAG);
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
 * Register auto-close: when the user types the closing `\` of an opening tag,
 * automatically insert `/tagname/` after the cursor.
 */
function registerAutoClose(context) {
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      if (isAutoClosing) return;
      const doc = event.document;
      if (doc.languageId !== 'short') return;

      if (event.contentChanges.length !== 1) return;
      const change = event.contentChanges[0];
      if (change.text !== '\\') return;

      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document !== doc) return;

      const insertedPos = change.range.start;
      const cursorPos = new vscode.Position(insertedPos.line, insertedPos.character + 1);

      const lineText = doc.lineAt(cursorPos.line).text.slice(0, cursorPos.character);
      const match = RE_OPENING_TAG_BEFORE_CURSOR.exec(lineText);
      if (!match) return;

      const tagName = match[1];
      const closing = `/${tagName}/`;

      const textAfterCursor = doc.getText().slice(doc.offsetAt(cursorPos));
      const escapedTagName = tagName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      if (new RegExp(`\\/${escapedTagName}\\/`).test(textAfterCursor)) return;

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
 * Register auto-delete closing tag: when an opening tag is broken or deleted
 * and the closing tag immediately follows (with only whitespace between), delete it.
 */
function registerAutoDeleteClose(context) {
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      if (isAutoDeletingClose || isAutoClosing) return;
      const doc = event.document;
      if (doc.languageId !== 'short') return;

      if (event.contentChanges.length !== 1) return;
      const change = event.contentChanges[0];
      if (change.rangeLength === 0) return;

      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document !== doc) return;

      const fullText = doc.getText();
      const startOffset = doc.offsetAt(change.range.start);

      const lookBehind = 80;
      const scanStart = Math.max(0, startOffset - lookBehind);
      const before = fullText.slice(scanStart, startOffset);
      const after  = fullText.slice(startOffset);

      const lastBackslash = before.lastIndexOf('\\');
      if (lastBackslash === -1) return;

      const fragment = before.slice(lastBackslash);
      const tagNameMatch = fragment.match(new RegExp(`^\\\\(${TAG_NAME})(?:\\\\)?$`));
      if (!tagNameMatch) return;

      const tagName = tagNameMatch[1];
      if (RE_PREFIXED.test(tagName)) return;

      if (fragment === `\\${tagName}\\`) return;

      const escapedName = tagName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const closingPattern = new RegExp(`^([\\s\\u200B\\uFEFF\\u00A0\\u2000-\\u200F\\u2028\\u2029]*)(\\/` + escapedName + `\\/)`);
      const closeMatch = closingPattern.exec(after);
      if (!closeMatch) return;

      const closingTagText = closeMatch[2];
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

/**
 * Register all auto-edit behaviours (smart enter, auto-close, auto-delete close).
 */
function registerAutoEdits(context) {
  registerSmartEnter(context);
  registerAutoClose(context);
  registerAutoDeleteClose(context);
}

module.exports = { registerAutoEdits };
