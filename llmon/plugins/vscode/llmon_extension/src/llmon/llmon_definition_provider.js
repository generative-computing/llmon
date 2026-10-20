// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { normalizeName } = require('./llmon_semantic_tokens');

// Matches any identifier-like word (including dots and colons for prefixed names)
const RE_REF_WORD = /[A-Za-z0-9_][A-Za-z0-9._:]*/g;

// Matches any LLMON structural token <|...|> — used to exclude cursor positions
// that are inside a structural token from being treated as references.
const RE_MTOKEN_BOUNDS = /<\|[^|]+\|>/g;

/**
 * Return true if `col` (character index on the line) falls inside any <|...|> token.
 */
function isInsideMToken(lineText, col) {
  RE_MTOKEN_BOUNDS.lastIndex = 0;
  let m;
  while ((m = RE_MTOKEN_BOUNDS.exec(lineText)) !== null) {
    if (col > m.index && col < m.index + m[0].length) return true;
  }
  return false;
}

/**
 * Given a document position, extract the full reference token the cursor is
 * touching. Returns { word, range } or null.
 *
 * Returns null for:
 * - positions inside structural tokens (<|open|>, <|close|>, etc.)
 * - the name segment of a tag declaration (directly adjacent to <|open|>/<|open_end|>/<|self_close|>)
 */
function getLlmonReferenceAtPosition(document, position) {
  const line = document.lineAt(position.line).text;
  const col  = position.character;

  if (isInsideMToken(line, col)) return null;

  RE_REF_WORD.lastIndex = 0;
  let m;
  while ((m = RE_REF_WORD.exec(line)) !== null) {
    const start = m.index;
    const end   = start + m[0].length;
    if (col >= start && col <= end) {
      // Check whether this word is the name part of a tag declaration.
      // In LLMON the name sits right after <|open|>, <|open_end|>, or before <|close|>/<|self_close|>.
      const before4 = line.slice(0, start);
      const after4  = line.slice(end);
      const isTagName =
        /(?:<\|open\|>|<\|open_end\|>)$/.test(before4) ||
        /^(?:<\|close\|>|<\|self_close\|>|<\|:\|>|<\|\.\|>)/.test(after4);
      if (isTagName) continue;

      const word  = m[0];
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
 * Find the offset of the first <|open|>tagName<|close|> for `tagName` in `text`.
 * Returns -1 if not found.
 */
function findLlmonOpeningTagOffset(text, tagName) {
  // Build a regex that matches the opening tag for this normalized name.
  // Each '.' or ':' in the canonical name may appear as <|.|>/<|:|> tokens OR
  // as bare . / : characters (for robustness), so we substitute them.
  const escapedParts = tagName.split(/([.:])/);
  const regexParts = escapedParts.map((part) => {
    if (part === '.') return '(?:<\\|\\.\\|>|\\.)';  // <|.|> or literal .
    if (part === ':') return '(?:<\\|:\\|>|:)';       // <|:|> or literal :
    return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  });
  const nameRegex = regexParts.join('');
  const re = new RegExp('<\\|open\\|>' + nameRegex + '<\\|close\\|>');
  const m = re.exec(text);
  return m ? m.index : -1;
}

const llmonDefinitionProvider = {
  provideDefinition(document, position) {
    if (document.languageId !== 'llmon') return null;

    const ref = getLlmonReferenceAtPosition(document, position);
    if (!ref) return null;

    const { word } = ref;
    const text   = document.getText();
    const offset = findLlmonOpeningTagOffset(text, word);
    if (offset === -1) return null;

    const targetPos = document.positionAt(offset);
    // The full tag token length: <|open|> + encoded name + <|close|>
    // Approximate by finding the end of the match.
    const re = new RegExp(`<\\|open\\|>${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:<\\|close\\|>|<\\|self_close\\|>)`);
    const tagMatch = re.exec(text.slice(offset));
    const tagLen   = tagMatch ? tagMatch[0].length : word.length + 8 + 9;
    const tagEnd   = document.positionAt(offset + tagLen);
    const targetRange = new vscode.Range(targetPos, tagEnd);

    return new vscode.Location(document.uri, targetRange);
  }
};

module.exports = {
  getLlmonReferenceAtPosition,
  findLlmonOpeningTagOffset,
  llmonDefinitionProvider,
};
