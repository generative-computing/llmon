// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');

// Broad identifier regex covering both prefixed and user-tag references (spaces allowed in middle)
const RE_REF_WORD = /[A-Za-z0-9_](?:[A-Za-z0-9._: ]*[A-Za-z0-9._:])?/g;

/**
 * Given a document position, extract the full reference token that the cursor
 * is touching. Returns {word, range} or null.
 * Returns null for tag declarations (\name\, /name/) — only bare-word references.
 */
function getReferenceAtPosition(document, position) {
  const line = document.lineAt(position.line).text;
  const col = position.character;

  RE_REF_WORD.lastIndex = 0;
  let m;
  while ((m = RE_REF_WORD.exec(line)) !== null) {
    const start = m.index;
    const end = start + m[0].length;
    if (col >= start && col <= end) {
      const before = start > 0 ? line[start - 1] : '';
      const after  = end < line.length ? line[end] : '';
      // Skip the name part of a tag declaration (\name\ or /name/)
      if ((before === '\\' || before === '/') && (after === '\\' || after === '/')) {
        continue;
      }
      const word = m[0];
      const range = new vscode.Range(
        new vscode.Position(position.line, start),
        new vscode.Position(position.line, end)
      );
      return { word, range };
    }
  }
  return null;
}

/**
 * Find the opening-tag definition (`\name\`) for `tagName` in `text`.
 * Returns the character offset of the start of the match, or -1.
 */
function findOpeningTagOffset(text, tagName) {
  const escaped = tagName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`\\\\${escaped}\\\\(?!\\/)`, 'g');
  const m = re.exec(text);
  return m ? m.index : -1;
}

const definitionProvider = {
  provideDefinition(document, position) {
    if (document.languageId !== 'short') return null;

    const ref = getReferenceAtPosition(document, position);
    if (!ref) return null;

    const { word } = ref;
    const text = document.getText();
    const offset = findOpeningTagOffset(text, word);
    if (offset === -1) return null;

    const targetPos = document.positionAt(offset);
    const tagEnd = document.positionAt(offset + word.length + 2);
    const targetRange = new vscode.Range(targetPos, tagEnd);

    return new vscode.Location(document.uri, targetRange);
  }
};

module.exports = { getReferenceAtPosition, findOpeningTagOffset, definitionProvider };
