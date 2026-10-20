// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { RE_M_OPEN, RE_M_SELF, normalizeName, collectCloseEvents } = require('./llmon_semantic_tokens');
const { TAG_PAIR_DECORATION, SCOPE_GUIDE_DECORATION } = require('../short/short_tag_pair_highlight');
const { findPairedTag } = require('../shared/tag_matching');

/**
 * Scan `text` and return all LLMON tag tokens in document order.
 * Each entry: { kind: 'open'|'close'|'self', name (normalized, or null for
 * a bare close), start, end }
 */
function collectLlmonTagTokens(text) {
  const tokens = [];
  let m;

  RE_M_OPEN.lastIndex = 0;
  while ((m = RE_M_OPEN.exec(text)) !== null) {
    tokens.push({
      kind: 'open',
      name: normalizeName(m[1]),
      start: m.index,
      end: m.index + m[0].length,
    });
  }
  RE_M_OPEN.lastIndex = 0;

  for (const ev of collectCloseEvents(text)) {
    tokens.push({
      kind: 'close',
      name: ev.name, // already normalized, or null for a bare close
      start: ev.offset,
      end: ev.offset + ev.length,
    });
  }

  RE_M_SELF.lastIndex = 0;
  while ((m = RE_M_SELF.exec(text)) !== null) {
    tokens.push({
      kind: 'self',
      name: normalizeName(m[1]),
      start: m.index,
      end: m.index + m[0].length,
    });
  }
  RE_M_SELF.lastIndex = 0;

  tokens.sort((a, b) => a.start - b.start);
  return tokens;
}

/**
 * Build scope guide ranges for lines between `openPos` and `closePos`.
 */
function buildLlmonScopeGuideRanges(doc, openPos, closePos) {
  const col = openPos.character;
  const ranges = [];
  for (let line = openPos.line + 1; line < closePos.line; line++) {
    const lineText = doc.lineAt(line).text;
    if (lineText.length > 0) {
      const pos = new vscode.Position(line, col);
      ranges.push(new vscode.Range(pos, pos));
    }
  }
  return ranges;
}

/**
 * Recompute and apply/clear the tag-pair decoration for a llmon editor.
 */
function updateLlmonTagPairHighlight(editor) {
  if (!editor || editor.document.languageId !== 'llmon') return;

  const doc = editor.document;
  const text = doc.getText();
  const cursorOffset = doc.offsetAt(editor.selection.active);

  const tokens = collectLlmonTagTokens(text);
  const paired = findPairedTag(tokens, cursorOffset);

  if (!paired) {
    editor.setDecorations(TAG_PAIR_DECORATION, []);
    editor.setDecorations(SCOPE_GUIDE_DECORATION, []);
    return;
  }

  const range = new vscode.Range(doc.positionAt(paired.start), doc.positionAt(paired.end));
  editor.setDecorations(TAG_PAIR_DECORATION, [range]);

  const cursorToken = tokens.find(t => cursorOffset >= t.start && cursorOffset <= t.end);

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

  if (closePos.line - openPos.line > 1) {
    editor.setDecorations(SCOPE_GUIDE_DECORATION, buildLlmonScopeGuideRanges(doc, openPos, closePos));
  } else {
    editor.setDecorations(SCOPE_GUIDE_DECORATION, []);
  }
}

module.exports = { collectLlmonTagTokens, updateLlmonTagPairHighlight };
