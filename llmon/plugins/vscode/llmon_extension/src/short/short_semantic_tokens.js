// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');

// Token type index layout:
//   0-4  : userTag0 … userTag4      (tag delimiters and name in \name\ / /name/ / \name/)
//   5-9  : userTagRef0 … userTagRef4 (bare-word references to user tags)
//   10   : instrRef   (validated instr:X reference — tag exists before reference)
//   11   : dataRef    (validated data:X reference)
//   12   : execRef    (validated exec:X reference)
//   13   : objectTag  (object tag)
//   14   : itemTag    (item tag)
//   15   : listTag    (list tag)
//   16   : intTag     (int tag)
//   17   : floatTag   (float tag)
//   18   : boolTag    (bool tag)
//   19   : noneTag    (none tag)
//   20   : inputTag   (input tag)
//   21   : outputTag  (output tag)
//   22   : objectRef  (object reference)
//   23   : itemRef    (item reference)
//   24   : listRef    (list reference)
//   25   : intRef     (int reference)
//   26   : floatRef   (float reference)
//   27   : boolRef    (bool reference)
//   28   : noneRef    (none reference)
//   29   : inputRef   (input reference)
//   30   : outputRef  (output reference)
const TOKEN_TYPES = [
  'userTag0', 'userTag1', 'userTag2', 'userTag3', 'userTag4',
  'userTagRef0', 'userTagRef1', 'userTagRef2', 'userTagRef3', 'userTagRef4',
  'instrRef', 'dataRef', 'execRef',
  'objectTag', 'itemTag', 'listTag', 'intTag', 'floatTag', 'boolTag', 'noneTag', 'inputTag', 'outputTag',
  'objectRef', 'itemRef', 'listRef', 'intRef', 'floatRef', 'boolRef', 'noneRef', 'inputRef', 'outputRef',
];
const TOKEN_MODIFIERS = [];
const LEGEND = new vscode.SemanticTokensLegend(TOKEN_TYPES, TOKEN_MODIFIERS);

const NUM_BUCKETS = 5;
const IDX_INSTR_REF  = 10;
const IDX_DATA_REF   = 11;
const IDX_EXEC_REF   = 12;
const IDX_OBJECT_TAG = 13;
const IDX_ITEM_TAG   = 14;
const IDX_LIST_TAG   = 15;
const IDX_INT_TAG    = 16;
const IDX_FLOAT_TAG  = 17;
const IDX_BOOL_TAG   = 18;
const IDX_NONE_TAG   = 19;
const IDX_INPUT_TAG  = 20;
const IDX_OUTPUT_TAG = 21;
const IDX_OBJECT_REF = 22;
const IDX_ITEM_REF   = 23;
const IDX_LIST_REF   = 24;
const IDX_INT_REF    = 25;
const IDX_FLOAT_REF  = 26;
const IDX_BOOL_REF   = 27;
const IDX_NONE_REF   = 28;
const IDX_INPUT_REF  = 29;
const IDX_OUTPUT_REF = 30;

// Map from base built-in name to tag/ref token type indices
const BUILTIN_TAG_MAP = new Map([
  ['object',      [IDX_OBJECT_TAG, IDX_OBJECT_REF]],
  ['item',        [IDX_ITEM_TAG,   IDX_ITEM_REF]],
  ['list',        [IDX_LIST_TAG,   IDX_LIST_REF]],
  ['int',         [IDX_INT_TAG,    IDX_INT_REF]],
  ['float',       [IDX_FLOAT_TAG,  IDX_FLOAT_REF]],
  ['bool',        [IDX_BOOL_TAG,   IDX_BOOL_REF]],
  ['none',        [IDX_NONE_TAG,   IDX_NONE_REF]],
  ['input',       [IDX_INPUT_TAG,  IDX_INPUT_REF]],
  ['output',      [IDX_OUTPUT_TAG, IDX_OUTPUT_REF]],
]);

// Built-in tag names (with or without colon suffix) — never user tags
const RE_PREFIXED = /^(?:instruction|instr|data|execution|exec|object|item|list|int|float|bool|none|input|output)(?::[A-Za-z0-9_]|$)/;

// Tag name pattern: starts with non-space word char, optionally followed by interior
// chars (including spaces) ending on a non-space word char.
// Matches: "foo", "foo bar", "instr:my tag", "a"
const TAG_NAME = '[A-Za-z0-9_](?:[A-Za-z0-9._: ]*[A-Za-z0-9._:])?';

// Matches user tag open:  \name\   (not followed by /)
const RE_TAG_OPEN = new RegExp(`\\\\(${TAG_NAME})\\\\(?!\\/)`, 'g');
// Matches user tag close: /name/
const RE_TAG_CLOSE = new RegExp(`\\/(${TAG_NAME})\\/`, 'g');
// Matches self-closing:   \name/
const RE_SELF_CLOSE = new RegExp(`(?<![A-Za-z0-9._:])\\\\(${TAG_NAME})\\/`, 'g');
// Matches bare identifier candidate (not adjacent to \ or /)
const RE_BARE_WORD = /(?<![\\\/])\b([A-Za-z0-9_][A-Za-z0-9._:]*)\b(?![\\\/])/g;

// Matches a prefixed bare-word reference (not inside tag delimiters)
const RE_PREFIXED_REF = /(?<![\\\/])\b((?:instruction|instr|data|execution|exec)(?::[A-Za-z0-9_][A-Za-z0-9._:]*)?)\b(?![\\\/])/g;

// Matches an opening tag for a prefixed name: \instr:m\ or \data:2\ etc.
const RE_PREFIXED_TAG_OPEN = new RegExp(`\\\\((?:instruction|instr|data|execution|exec|object|item|list|int|float|bool|none|input|output)(?::${TAG_NAME})?)\\\\(?!\\/)`, 'g');

// Matches any opening/closing/self-closing tag for the new structural built-ins (no label suffix)
const RE_BUILTIN_TAG_OPEN   = /\\(object|item|list|int|float|bool|none|input|output)\\(?!\/)/g;
const RE_BUILTIN_TAG_CLOSE  = /\/(object|item|list|int|float|bool|none|input|output)\//g;
const RE_BUILTIN_SELF_CLOSE = /(?<![A-Za-z0-9._:])\\(object|item|list|int|float|bool|none|input|output)\//g;

// Matches a bare-word or reference to a new structural built-in
const RE_BUILTIN_REF = /(?<![\\\/])\b(object|item|list|int|float|bool|none|input|output)\b(?![\\\/])/g;

/**
 * Build bucket map: first pass over all tag opens/closes/self-closes, assigning
 * each unique non-prefixed user tag name a bucket index in encounter order.
 */
function buildBucketMap(text) {
  const buckets = new Map();
  let next = 0;

  function assign(name) {
    if (RE_PREFIXED.test(name)) return;
    if (!buckets.has(name)) {
      buckets.set(name, next % NUM_BUCKETS);
      next++;
    }
  }

  const candidates = [];
  let m;

  RE_TAG_OPEN.lastIndex = 0;
  while ((m = RE_TAG_OPEN.exec(text)) !== null) candidates.push({ offset: m.index, name: m[1] });

  RE_TAG_CLOSE.lastIndex = 0;
  while ((m = RE_TAG_CLOSE.exec(text)) !== null) candidates.push({ offset: m.index, name: m[1] });

  RE_SELF_CLOSE.lastIndex = 0;
  while ((m = RE_SELF_CLOSE.exec(text)) !== null) candidates.push({ offset: m.index, name: m[1] });

  candidates.sort((a, b) => a.offset - b.offset);
  for (const c of candidates) assign(c.name);

  return buckets;
}

/**
 * Convert an absolute character offset in `text` to {line, char}.
 * Precompute line-start offsets for efficiency.
 */
function buildLineIndex(text) {
  const starts = [0];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '\n') starts.push(i + 1);
  }
  return starts;
}

function offsetToPos(lineStarts, offset) {
  let lo = 0, hi = lineStarts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (lineStarts[mid] <= offset) lo = mid;
    else hi = mid - 1;
  }
  return { line: lo, char: offset - lineStarts[lo] };
}

/**
 * Build a map of tagName -> earliest opening-tag offset for ALL tag forms.
 * Used to enforce the "tag must exist before its reference" rule.
 */
function buildFirstOpenMap(text) {
  const firstOpen = new Map();

  function record(name, offset) {
    if (!firstOpen.has(name) || offset < firstOpen.get(name)) {
      firstOpen.set(name, offset);
    }
  }

  let m;

  RE_TAG_OPEN.lastIndex = 0;
  while ((m = RE_TAG_OPEN.exec(text)) !== null) record(m[1], m.index);

  RE_PREFIXED_TAG_OPEN.lastIndex = 0;
  while ((m = RE_PREFIXED_TAG_OPEN.exec(text)) !== null) record(m[1], m.index);

  RE_BUILTIN_TAG_OPEN.lastIndex = 0;
  while ((m = RE_BUILTIN_TAG_OPEN.exec(text)) !== null) record(m[1], m.index);

  RE_BUILTIN_SELF_CLOSE.lastIndex = 0;
  while ((m = RE_BUILTIN_SELF_CLOSE.exec(text)) !== null) record(m[1], m.index);

  return firstOpen;
}

/**
 * Emit semantic tokens for all tag occurrences and bare-word references.
 */
function buildTokens(text, buckets, lineStarts, firstOpen) {
  const tokens = [];

  function push(offset, length, typeIndex) {
    tokens.push({ offset, length, typeIndex });
  }

  let m;

  RE_TAG_OPEN.lastIndex = 0;
  while ((m = RE_TAG_OPEN.exec(text)) !== null) {
    const name = m[1];
    if (RE_PREFIXED.test(name)) continue;
    const bucket = buckets.get(name);
    if (bucket === undefined) continue;
    const base = m.index;
    push(base, 1, bucket);
    push(base + 1, name.length, bucket);
    push(base + 1 + name.length, 1, bucket);
  }

  RE_TAG_CLOSE.lastIndex = 0;
  while ((m = RE_TAG_CLOSE.exec(text)) !== null) {
    const name = m[1];
    if (RE_PREFIXED.test(name)) continue;
    const bucket = buckets.get(name);
    if (bucket === undefined) continue;
    const base = m.index;
    push(base, 1, bucket);
    push(base + 1, name.length, bucket);
    push(base + 1 + name.length, 1, bucket);
  }

  RE_SELF_CLOSE.lastIndex = 0;
  while ((m = RE_SELF_CLOSE.exec(text)) !== null) {
    const name = m[1];
    if (RE_PREFIXED.test(name)) continue;
    const bucket = buckets.get(name);
    if (bucket === undefined) continue;
    const base = m.index;
    push(base, 1, bucket);
    push(base + 1, name.length, bucket);
    push(base + 1 + name.length, 1, bucket);
  }

  // New structural built-in tags (object, item, list, int, float, bool, none, input, output)
  function emitBuiltinTag(base, name) {
    const entry = BUILTIN_TAG_MAP.get(name);
    if (!entry) return;
    const tagIdx = entry[0];
    push(base, 1, tagIdx);
    push(base + 1, name.length, tagIdx);
    push(base + 1 + name.length, 1, tagIdx);
  }

  RE_BUILTIN_TAG_OPEN.lastIndex = 0;
  while ((m = RE_BUILTIN_TAG_OPEN.exec(text)) !== null) emitBuiltinTag(m.index, m[1]);

  RE_BUILTIN_TAG_CLOSE.lastIndex = 0;
  while ((m = RE_BUILTIN_TAG_CLOSE.exec(text)) !== null) emitBuiltinTag(m.index, m[1]);

  RE_BUILTIN_SELF_CLOSE.lastIndex = 0;
  while ((m = RE_BUILTIN_SELF_CLOSE.exec(text)) !== null) emitBuiltinTag(m.index, m[1]);

  const delimRanges = tokens.map(t => [t.offset, t.offset + t.length]);

  function isInsideDelimiter(offset, len) {
    const end = offset + len;
    for (const [s, e] of delimRanges) {
      if (offset >= s && end <= e) return true;
    }
    return false;
  }

  const lines = text.split('\n');
  let lineOffset = 0;
  for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
    const line = lines[lineIdx];

    RE_BARE_WORD.lastIndex = 0;
    while ((m = RE_BARE_WORD.exec(line)) !== null) {
      const word = m[1];
      if (RE_PREFIXED.test(word)) continue;
      if (!buckets.has(word)) continue;
      const absOffset = lineOffset + m.index;
      if (isInsideDelimiter(absOffset, m[0].length)) continue;
      const openOffset = firstOpen.get(word);
      if (openOffset === undefined || openOffset >= absOffset) continue;
      const bucket = buckets.get(word);
      tokens.push({ offset: absOffset, length: word.length, typeIndex: NUM_BUCKETS + bucket });
    }

    RE_PREFIXED_REF.lastIndex = 0;
    while ((m = RE_PREFIXED_REF.exec(line)) !== null) {
      const word = m[1];
      const absOffset = lineOffset + m.index;
      if (isInsideDelimiter(absOffset, word.length)) continue;
      const openOffset = firstOpen.get(word);
      if (openOffset === undefined || openOffset >= absOffset) continue;
      let typeIndex;
      if (/^(?:instruction|instr)/.test(word)) typeIndex = IDX_INSTR_REF;
      else if (/^data/.test(word))             typeIndex = IDX_DATA_REF;
      else                                     typeIndex = IDX_EXEC_REF;
      tokens.push({ offset: absOffset, length: word.length, typeIndex });
    }

    RE_BUILTIN_REF.lastIndex = 0;
    while ((m = RE_BUILTIN_REF.exec(line)) !== null) {
      const word = m[1];
      const absOffset = lineOffset + m.index;
      if (isInsideDelimiter(absOffset, word.length)) continue;
      const openOffset = firstOpen.get(word);
      if (openOffset === undefined || openOffset >= absOffset) continue;
      const entry = BUILTIN_TAG_MAP.get(word);
      if (!entry) continue;
      tokens.push({ offset: absOffset, length: word.length, typeIndex: entry[1] });
    }

    lineOffset += line.length + 1;
  }

  return tokens;
}

const provider = {
  provideDocumentSemanticTokens(document) {
    const text = document.getText();
    const buckets = buildBucketMap(text);
    const builder = new vscode.SemanticTokensBuilder(LEGEND);

    const firstOpen = buildFirstOpenMap(text);
    if (buckets.size === 0 && firstOpen.size === 0) return builder.build();

    const lineStarts = buildLineIndex(text);
    const tokens = buildTokens(text, buckets, lineStarts, firstOpen);

    tokens.sort((a, b) => a.offset - b.offset);

    for (const { offset, length, typeIndex } of tokens) {
      const { line, char } = offsetToPos(lineStarts, offset);
      builder.push(line, char, length, typeIndex, 0);
    }

    return builder.build();
  }
};

module.exports = { LEGEND, RE_PREFIXED, RE_TAG_OPEN, RE_TAG_CLOSE, RE_SELF_CLOSE, BUILTIN_TAG_MAP, provider };
