// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { RE_TAG_OPEN, RE_TAG_CLOSE, RE_SELF_CLOSE } = require('./short_semantic_tokens');
const { findPairedTag } = require('../shared/tag_matching');

// Decoration type for tag-pair bracket matching (background only, no foreground change).
const TAG_PAIR_DECORATION = vscode.window.createTextEditorDecorationType({
  backgroundColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
  borderRadius: '2px',
});

// Decoration type for the vertical scope guide line drawn between matched open/close tags.
const SCOPE_GUIDE_DECORATION = vscode.window.createTextEditorDecorationType({
  borderStyle: 'solid',
  borderWidth: '0 0 0 1px',
  borderColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
});

/**
 * Scan `text` and return all tag tokens in document order.
 * Each entry: { kind: 'open'|'close'|'self', name, start, end }
 */
function collectTagTokens(text) {
  const tokens = [];
  let m;

  RE_TAG_OPEN.lastIndex = 0;
  while ((m = RE_TAG_OPEN.exec(text)) !== null) {
    tokens.push({ kind: 'open', name: m[1], start: m.index, end: m.index + m[0].length });
  }

  RE_TAG_CLOSE.lastIndex = 0;
  while ((m = RE_TAG_CLOSE.exec(text)) !== null) {
    tokens.push({ kind: 'close', name: m[1], start: m.index, end: m.index + m[0].length });
  }

  RE_SELF_CLOSE.lastIndex = 0;
  while ((m = RE_SELF_CLOSE.exec(text)) !== null) {
    tokens.push({ kind: 'self', name: m[1], start: m.index, end: m.index + m[0].length });
  }

  tokens.sort((a, b) => a.start - b.start);
  return tokens;
}

/**
 * Build scope guide ranges for lines between `openPos` and `closePos`.
 * The guide is a zero-width left-border drawn at the indentation column of the open tag.
 */
function buildScopeGuideRanges(doc, openPos, closePos) {
  const col = openPos.character;
  const ranges = [];
  for (let line = openPos.line + 1; line < closePos.line; line++) {
    const lineText = doc.lineAt(line).text;
    // Only draw if the line has content at or past the guide column (skip blank lines).
    if (lineText.length > 0) {
      const pos = new vscode.Position(line, col);
      ranges.push(new vscode.Range(pos, pos));
    }
  }
  return ranges;
}

/**
 * Recompute and apply/clear the tag-pair decoration for `editor`.
 */
function updateTagPairHighlight(editor) {
  if (!editor || editor.document.languageId !== 'short') return;

  const doc = editor.document;
  const text = doc.getText();
  const cursorOffset = doc.offsetAt(editor.selection.active);

  const tokens = collectTagTokens(text);
  const paired = findPairedTag(tokens, cursorOffset);

  if (!paired) {
    editor.setDecorations(TAG_PAIR_DECORATION, []);
    editor.setDecorations(SCOPE_GUIDE_DECORATION, []);
    return;
  }

  const range = new vscode.Range(doc.positionAt(paired.start), doc.positionAt(paired.end));
  editor.setDecorations(TAG_PAIR_DECORATION, [range]);

  // Determine which end is the open tag to anchor the guide column.
  const cursorToken = tokens.find(t => {
    const off = doc.offsetAt(editor.selection.active);
    return off >= t.start && off <= t.end;
  });

  let openOffset, closeOffset;
  if (cursorToken && cursorToken.kind === 'open') {
    openOffset  = cursorToken.start;
    closeOffset = paired.start;
  } else {
    openOffset  = paired.start;
    closeOffset = cursorToken ? cursorToken.start : paired.start;
  }

  const openPos  = doc.positionAt(openOffset);
  const closePos = doc.positionAt(closeOffset);

  // Only draw the guide when the pair spans more than one line.
  if (closePos.line - openPos.line > 1) {
    editor.setDecorations(SCOPE_GUIDE_DECORATION, buildScopeGuideRanges(doc, openPos, closePos));
  } else {
    editor.setDecorations(SCOPE_GUIDE_DECORATION, []);
  }
}

module.exports = { TAG_PAIR_DECORATION, SCOPE_GUIDE_DECORATION, updateTagPairHighlight };
