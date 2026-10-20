// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');

// Reuse the same token type layout as Shorthand:
//   0-4  : userTag0 … userTag4
//   5-9  : userTagRef0 … userTagRef4
//   10   : instrRef
//   11   : dataRef
//   12   : execRef
//   13   : objectTag
//   14   : itemTag
//   15   : listTag
//   16   : intTag
//   17   : floatTag
//   18   : boolTag
//   19   : noneTag
//   20   : inputTag
//   21   : outputTag
//   22   : objectRef
//   23   : itemRef
//   24   : listRef
//   25   : intRef
//   26   : floatRef
//   27   : boolRef
//   28   : noneRef
//   29   : inputRef
//   30   : outputRef
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
  ['object',  [IDX_OBJECT_TAG, IDX_OBJECT_REF]],
  ['item',    [IDX_ITEM_TAG,   IDX_ITEM_REF]],
  ['list',    [IDX_LIST_TAG,   IDX_LIST_REF]],
  ['int',     [IDX_INT_TAG,    IDX_INT_REF]],
  ['float',   [IDX_FLOAT_TAG,  IDX_FLOAT_REF]],
  ['bool',    [IDX_BOOL_TAG,   IDX_BOOL_REF]],
  ['none',    [IDX_NONE_TAG,   IDX_NONE_REF]],
  ['input',   [IDX_INPUT_TAG,  IDX_INPUT_REF]],
  ['output',  [IDX_OUTPUT_TAG, IDX_OUTPUT_REF]],
]);

// Built-in tag prefixes — never user tags
const RE_PREFIXED = /^(?:instruction|instr|data|execution|exec|object|item|list|int|float|bool|none|input|output)(?:[.:]|$)/;

// LLMON tag patterns — each matches the full atomic token sequence.
// Capture group 1 = raw name (may contain <|.|> and <|:|>).

// <|open|>name<|close|>   — opening tag (not self-closing)
// Name may contain <|.|> and <|:|> separator tokens but must not swallow
// another structural token (<|open|>, <|open_end|>, <|close|>, <|self_close|>) —
// otherwise a bare <|open_end|> later in the document gets absorbed as if it
// were part of this tag's name, hiding a legitimate bare-close match.
const RE_M_OPEN      = /<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g;
// <|open_end|>name<|close|> — closing tag
const RE_M_CLOSE_TAG = /<\|open_end\|>((?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g;
// <|open|>name<|self_close|> — self-closing tag
const RE_M_SELF      = /<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|self_close\|>/g;
// <|open_end|> — bare closing tag (token-saving form, no name/close required)
const RE_M_CLOSE_BARE = /<\|open_end\|>/g;

// Lengths of the structural tokens
const LEN_OPEN      = '<|open|>'.length;       // 8
const LEN_OPEN_END  = '<|open_end|>'.length;   // 12
const LEN_CLOSE_T   = '<|close|>'.length;      // 9
const LEN_SELF_C    = '<|self_close|>'.length; // 14
const LEN_DOT       = '<|.|>'.length;          // 5
const LEN_COLON     = '<|:|>'.length;          // 5

/**
 * Normalize a raw LLMON name (which may contain <|.|> and <|:|>) to its
 * canonical dot/colon form used for bucket assignment and lookups.
 */
function normalizeName(raw) {
  return raw.replace(/<\|\.\|>/g, '.').replace(/<\|:\|>/g, ':');
}

/**
 * Collect all closing-tag events (named and bare) in document order.
 * Named closes: { offset, length, name, rawName }. Bare closes (token-saving
 * form <|open_end|> with no name/close) match whatever is on top of the
 * open-tag stack, so they carry name: null.
 */
function collectCloseEvents(text) {
  const events = [];
  let m;

  const namedRanges = [];
  RE_M_CLOSE_TAG.lastIndex = 0;
  while ((m = RE_M_CLOSE_TAG.exec(text)) !== null) {
    namedRanges.push(m.index);
    events.push({ offset: m.index, length: m[0].length, name: normalizeName(m[1]), rawName: m[1] });
  }

  RE_M_CLOSE_BARE.lastIndex = 0;
  while ((m = RE_M_CLOSE_BARE.exec(text)) !== null) {
    if (namedRanges.includes(m.index)) continue;
    events.push({ offset: m.index, length: m[0].length, name: null, rawName: null });
  }

  events.sort((a, b) => a.offset - b.offset);
  return events;
}

/**
 * Build bucket map: assign each unique non-prefixed user tag name a color
 * bucket (0-4) in document-order of first appearance.
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

  RE_M_OPEN.lastIndex = 0;
  while ((m = RE_M_OPEN.exec(text)) !== null) {
    candidates.push({ offset: m.index, name: normalizeName(m[1]) });
  }
  RE_M_CLOSE_TAG.lastIndex = 0;
  while ((m = RE_M_CLOSE_TAG.exec(text)) !== null) {
    candidates.push({ offset: m.index, name: normalizeName(m[1]) });
  }
  RE_M_SELF.lastIndex = 0;
  while ((m = RE_M_SELF.exec(text)) !== null) {
    candidates.push({ offset: m.index, name: normalizeName(m[1]) });
  }

  candidates.sort((a, b) => a.offset - b.offset);
  for (const c of candidates) assign(c.name);

  return buckets;
}

/** Precompute line-start offsets for efficient offset→{line,char} conversion. */
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
 * Build map of normalized tagName → earliest opening-tag absolute offset.
 * Used to enforce "tag must be defined before its reference".
 */
function buildFirstOpenMap(text) {
  const firstOpen = new Map();

  function record(name, offset) {
    if (!firstOpen.has(name) || offset < firstOpen.get(name)) {
      firstOpen.set(name, offset);
    }
  }

  let m;
  RE_M_OPEN.lastIndex = 0;
  while ((m = RE_M_OPEN.exec(text)) !== null) record(normalizeName(m[1]), m.index);

  RE_M_SELF.lastIndex = 0;
  while ((m = RE_M_SELF.exec(text)) !== null) record(normalizeName(m[1]), m.index);

  return firstOpen;
}

/**
 * Given the raw name portion of a tag (may contain <|.|> and <|:|>),
 * emit semantic tokens for each piece:
 *   - plain text segments → bucket color
 *   - <|.|> and <|:|> separator tokens → bucket color
 * Returns array of {relOffset, length} relative to `nameStart`.
 */
function nameSegments(raw) {
  const segments = [];
  let pos = 0;
  while (pos < raw.length) {
    const dot = raw.indexOf('<|.|>', pos);
    const colon = raw.indexOf('<|:|>', pos);
    // Find the nearest separator
    let sep = -1;
    let sepLen = 0;
    if (dot !== -1 && (colon === -1 || dot <= colon)) { sep = dot; sepLen = LEN_DOT; }
    else if (colon !== -1) { sep = colon; sepLen = LEN_COLON; }

    if (sep === -1) {
      // Rest is plain text
      if (pos < raw.length) segments.push({ relOffset: pos, length: raw.length - pos });
      break;
    }
    if (sep > pos) segments.push({ relOffset: pos, length: sep - pos });
    segments.push({ relOffset: sep, length: sepLen });
    pos = sep + sepLen;
  }
  return segments;
}

/**
 * Resolve, for each bare closing tag (token-saving form <|open_end|> with no
 * name/close), the normalized name of the tag it closes — i.e. whatever is
 * innermost on the open-tag stack at that point. Returns a Map from the bare
 * close's absolute offset to that resolved name (or undefined if orphaned).
 */
function resolveBareCloseNames(text) {
  const events = [];
  let m;

  RE_M_OPEN.lastIndex = 0;
  while ((m = RE_M_OPEN.exec(text)) !== null) {
    events.push({ offset: m.index, type: 'open', name: normalizeName(m[1]) });
  }

  for (const ev of collectCloseEvents(text)) {
    events.push({ offset: ev.offset, type: 'close', name: ev.name });
  }

  events.sort((a, b) => a.offset - b.offset);

  const stack = [];
  const bareCloseNames = new Map();

  for (const ev of events) {
    if (ev.type === 'open') {
      stack.push(ev.name);
    } else if (ev.name === null) {
      const top = stack.pop();
      if (top !== undefined) bareCloseNames.set(ev.offset, top);
    } else {
      for (let i = stack.length - 1; i >= 0; i--) {
        if (stack[i] === ev.name) { stack.splice(i); break; }
      }
    }
  }

  return bareCloseNames;
}

/**
 * Emit semantic tokens for all LLMON tag occurrences and bare-word references.
 */
function buildTokens(text, buckets, firstOpen) {
  const tokens = [];

  function push(offset, length, typeIndex) {
    tokens.push({ offset, length, typeIndex });
  }

  /**
   * Emit tokens for a complete tag match:
   *   prefixLen — length of the leading token (<|open|> etc.)
   *   raw       — captured name string
   *   nameStart — absolute offset of the first char of raw
   *   suffixLen — length of the trailing token (<|close|> etc.)
   *   typeIndex — semantic token type index
   */
  function emitTag(prefixOffset, prefixLen, raw, suffixLen, typeIndex) {
    push(prefixOffset, prefixLen, typeIndex);
    const nameStart = prefixOffset + prefixLen;
    for (const seg of nameSegments(raw)) {
      push(nameStart + seg.relOffset, seg.length, typeIndex);
    }
    push(nameStart + raw.length, suffixLen, typeIndex);
  }

  let m;

  // Opening tags
  RE_M_OPEN.lastIndex = 0;
  while ((m = RE_M_OPEN.exec(text)) !== null) {
    const name = normalizeName(m[1]);
    if (RE_PREFIXED.test(name)) continue;
    const bucket = buckets.get(name);
    if (bucket === undefined) continue;
    emitTag(m.index, LEN_OPEN, m[1], LEN_CLOSE_T, bucket);
  }

  // Closing tags
  RE_M_CLOSE_TAG.lastIndex = 0;
  while ((m = RE_M_CLOSE_TAG.exec(text)) !== null) {
    const name = normalizeName(m[1]);
    if (RE_PREFIXED.test(name)) continue;
    const bucket = buckets.get(name);
    if (bucket === undefined) continue;
    emitTag(m.index, LEN_OPEN_END, m[1], LEN_CLOSE_T, bucket);
  }

  // Self-closing tags
  RE_M_SELF.lastIndex = 0;
  while ((m = RE_M_SELF.exec(text)) !== null) {
    const name = normalizeName(m[1]);
    if (RE_PREFIXED.test(name)) continue;
    const bucket = buckets.get(name);
    if (bucket === undefined) continue;
    emitTag(m.index, LEN_OPEN, m[1], LEN_SELF_C, bucket);
  }

  // Bare closing tags (token-saving form <|open_end|>) — colored to match
  // whatever tag they close (the innermost currently-open tag).
  const bareCloseNames = resolveBareCloseNames(text);
  RE_M_CLOSE_BARE.lastIndex = 0;
  while ((m = RE_M_CLOSE_BARE.exec(text)) !== null) {
    const name = bareCloseNames.get(m.index);
    if (name === undefined) continue;
    if (RE_PREFIXED.test(name)) continue;
    const bucket = buckets.get(name);
    if (bucket === undefined) continue;
    push(m.index, m[0].length, bucket);
  }
  RE_M_CLOSE_BARE.lastIndex = 0;

  // New structural built-in tags (object, item, list, int, float, bool, none, input, output)
  function emitBuiltinMTag(prefixOffset, prefixLen, raw, suffixLen, tagIdx) {
    push(prefixOffset, prefixLen, tagIdx);
    const nameStart = prefixOffset + prefixLen;
    for (const seg of nameSegments(raw)) {
      push(nameStart + seg.relOffset, seg.length, tagIdx);
    }
    push(nameStart + raw.length, suffixLen, tagIdx);
  }

  RE_M_OPEN.lastIndex = 0;
  while ((m = RE_M_OPEN.exec(text)) !== null) {
    const name = normalizeName(m[1]);
    const entry = BUILTIN_TAG_MAP.get(name);
    if (!entry) continue;
    emitBuiltinMTag(m.index, LEN_OPEN, m[1], LEN_CLOSE_T, entry[0]);
  }

  RE_M_CLOSE_TAG.lastIndex = 0;
  while ((m = RE_M_CLOSE_TAG.exec(text)) !== null) {
    const name = normalizeName(m[1]);
    const entry = BUILTIN_TAG_MAP.get(name);
    if (!entry) continue;
    emitBuiltinMTag(m.index, LEN_OPEN_END, m[1], LEN_CLOSE_T, entry[0]);
  }

  RE_M_SELF.lastIndex = 0;
  while ((m = RE_M_SELF.exec(text)) !== null) {
    const name = normalizeName(m[1]);
    const entry = BUILTIN_TAG_MAP.get(name);
    if (!entry) continue;
    emitBuiltinMTag(m.index, LEN_OPEN, m[1], LEN_SELF_C, entry[0]);
  }

  // Bare closing tags that close a built-in structural tag.
  RE_M_CLOSE_BARE.lastIndex = 0;
  while ((m = RE_M_CLOSE_BARE.exec(text)) !== null) {
    const name = bareCloseNames.get(m.index);
    if (name === undefined) continue;
    const entry = BUILTIN_TAG_MAP.get(name);
    if (!entry) continue;
    push(m.index, m[0].length, entry[0]);
  }
  RE_M_CLOSE_BARE.lastIndex = 0;

  // Build a set of "inside-token" ranges to skip when looking for references.
  // Any position inside <|...|> is not a bare-word reference.
  const tokenRanges = tokens.map(t => [t.offset, t.offset + t.length]);

  function isInsideToken(offset, len) {
    const end = offset + len;
    for (const [s, e] of tokenRanges) {
      if (offset >= s && end <= e) return true;
    }
    return false;
  }

  // Also need to skip text inside LLMON structural tokens (<|open|>, etc.)
  // when scanning for bare-word references.
  const RE_MTOKEN = /<\|[^|]+\|>/g;
  const mtokenRanges = [];
  RE_MTOKEN.lastIndex = 0;
  while ((m = RE_MTOKEN.exec(text)) !== null) {
    mtokenRanges.push([m.index, m.index + m[0].length]);
  }

  function isInsideMToken(absOffset, len) {
    const end = absOffset + len;
    for (const [s, e] of mtokenRanges) {
      if (absOffset >= s && end <= e) return true;
      if (absOffset < e && end > s) return true; // overlap
    }
    return false;
  }

  // Plain-text reference patterns (for references written with literal . and :)
  const RE_BARE_WORD    = /(?<![\\\/])\b([A-Za-z0-9_][A-Za-z0-9._:]*)\b(?![\\\/])/g;
  const RE_PREFIXED_REF = /(?<![\\\/])\b((?:instruction|instr|data|execution|exec)(?::[A-Za-z0-9_][A-Za-z0-9._:]*)?)\b(?![\\\/])/g;

  // Tokenized reference pattern: matches a prefixed name written with <|:|> and <|.|> tokens,
  // e.g. instr<|:|>b  or  exec<|:|>x<|.|>instr  — but NOT inside a tag (i.e. not preceded by <|open|>/<|open_end|>)
  // Captured group 1 = raw name (may contain <|.|>/<|:|> tokens)
  const RE_LLMON_REF = /(?<!<\|open(?:_end)?\|>)((?:instruction|instr|data|execution|exec)(?:(?:<\|:\|>|<\|\.\|>)[A-Za-z0-9_][A-Za-z0-9._:]*)+)/g;

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
      if (isInsideToken(absOffset, m[0].length)) continue;
      if (isInsideMToken(absOffset, m[0].length)) continue;
      const openOffset = firstOpen.get(word);
      if (openOffset === undefined || openOffset >= absOffset) continue;
      const bucket = buckets.get(word);
      tokens.push({ offset: absOffset, length: word.length, typeIndex: NUM_BUCKETS + bucket });
    }

    RE_PREFIXED_REF.lastIndex = 0;
    while ((m = RE_PREFIXED_REF.exec(line)) !== null) {
      const word = m[1];
      const absOffset = lineOffset + m.index;
      if (isInsideToken(absOffset, word.length)) continue;
      if (isInsideMToken(absOffset, word.length)) continue;
      const openOffset = firstOpen.get(word);
      if (openOffset === undefined || openOffset >= absOffset) continue;
      let typeIndex;
      if (/^(?:instruction|instr)/.test(word)) typeIndex = IDX_INSTR_REF;
      else if (/^data/.test(word))             typeIndex = IDX_DATA_REF;
      else                                     typeIndex = IDX_EXEC_REF;
      tokens.push({ offset: absOffset, length: word.length, typeIndex });
    }

    // Plain-text references to new structural built-ins
    const RE_BUILTIN_REF = /(?<![\\\/])\b(object|item|list|int|float|bool|none|input|output)\b(?![\\\/])/g;
    RE_BUILTIN_REF.lastIndex = 0;
    while ((m = RE_BUILTIN_REF.exec(line)) !== null) {
      const word = m[1];
      const absOffset = lineOffset + m.index;
      if (isInsideToken(absOffset, word.length)) continue;
      if (isInsideMToken(absOffset, word.length)) continue;
      const openOffset = firstOpen.get(word);
      if (openOffset === undefined || openOffset >= absOffset) continue;
      const entry = BUILTIN_TAG_MAP.get(word);
      if (!entry) continue;
      tokens.push({ offset: absOffset, length: word.length, typeIndex: entry[1] });
    }

    lineOffset += line.length + 1;
  }

  // Tokenized references: instr<|:|>b, exec<|:|>x<|.|>instr, etc.
  // These span LLMON tokens so must be scanned against the full text, not line-by-line.
  RE_LLMON_REF.lastIndex = 0;
  while ((m = RE_LLMON_REF.exec(text)) !== null) {
    const raw = m[1];
    const absOffset = m.index;
    // Skip if inside an existing tag token range
    if (isInsideToken(absOffset, raw.length)) continue;
    const normalized = normalizeName(raw);
    const openOffset = firstOpen.get(normalized);
    if (openOffset === undefined || openOffset >= absOffset) continue;
    let typeIndex;
    if (/^(?:instruction|instr)/.test(normalized)) typeIndex = IDX_INSTR_REF;
    else if (/^data/.test(normalized))             typeIndex = IDX_DATA_REF;
    else                                           typeIndex = IDX_EXEC_REF;
    // Emit tokens for each segment (plain text and <|:|>/<|.|> tokens)
    for (const seg of nameSegments(raw)) {
      tokens.push({ offset: absOffset + seg.relOffset, length: seg.length, typeIndex });
    }
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
    const tokens = buildTokens(text, buckets, firstOpen);

    tokens.sort((a, b) => a.offset - b.offset);

    for (const { offset, length, typeIndex } of tokens) {
      const { line, char } = offsetToPos(lineStarts, offset);
      builder.push(line, char, length, typeIndex, 0);
    }

    return builder.build();
  }
};

module.exports = {
  LEGEND,
  RE_M_OPEN,
  RE_M_CLOSE_TAG,
  RE_M_SELF,
  RE_M_CLOSE_BARE,
  normalizeName,
  collectCloseEvents,
  resolveBareCloseNames,
  BUILTIN_TAG_MAP,
  provider,
};
