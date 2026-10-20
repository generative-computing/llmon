// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { checkBalance, detectIndentUnit, resolveIndentDepths } = require('../shared/tag_matching');

// LLMON structural token lengths
const LEN_OPEN     = '<|open|>'.length;      // 8
const LEN_OPEN_END = '<|open_end|>'.length;  // 12
const LEN_CLOSE_T  = '<|close|>'.length;     // 9
const LEN_SELF_C   = '<|self_close|>'.length;// 14

// Opening tag:   <|open|>name<|close|>
// Name may contain <|.|> and <|:|> separator tokens but must not swallow
// another structural token (<|open|>, <|open_end|>, <|close|>, <|self_close|>) —
// otherwise a bare <|open_end|> later in the document gets absorbed as if it
// were part of this tag's name, hiding a legitimate bare-close match.
const RE_M_OPEN      = /<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g;
// Closing tag:   <|open_end|>name<|close|>
const RE_M_CLOSE_TAG = /<\|open_end\|>((?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g;
// Self-closing:  <|open|>name<|self_close|>
const RE_M_SELF      = /<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|self_close\|>/g;
// Bare closing tag (token-saving form): <|open_end|> with no name/close
const RE_M_CLOSE_BARE = /<\|open_end\|>/g;

// Name ending with <|:|> and nothing after (empty label): <|open|>foo<|:|><|close|>
const RE_EMPTY_COLON_OPEN  = /<\|open\|>((?:[^<]|<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>)*?<\|:\|>)<\|close\|>/g;
const RE_EMPTY_COLON_CLOSE = /<\|open_end\|>((?:[^<]|<\|(?!close\||open\||open_end\|)[^|]+\|>)*?<\|:\|>)<\|close\|>/g;
const RE_EMPTY_COLON_SELF  = /<\|open\|>((?:[^<]|<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>)*?<\|:\|>)<\|self_close\|>/g;

function normalizeName(raw) {
  return raw.replace(/<\|\.\|>/g, '.').replace(/<\|:\|>/g, ':');
}

function buildLineIndex(text) {
  const starts = [0];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '\n') starts.push(i + 1);
  }
  return starts;
}

function offsetToLineCol(lineStarts, offset) {
  let lo = 0, hi = lineStarts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (lineStarts[mid] <= offset) lo = mid;
    else hi = mid - 1;
  }
  return { line: lo, col: offset - lineStarts[lo] };
}

/**
 * Collect all tag events with absolute offsets.
 */
function collectTagEvents(text) {
  const events = [];
  let m;

  RE_M_OPEN.lastIndex = 0;
  while ((m = RE_M_OPEN.exec(text)) !== null) {
    events.push({
      type: 'open',
      name: normalizeName(m[1]),
      rawName: m[1],
      offset: m.index,
      length: m[0].length,
    });
  }

  const namedCloseOffsets = [];
  RE_M_CLOSE_TAG.lastIndex = 0;
  while ((m = RE_M_CLOSE_TAG.exec(text)) !== null) {
    namedCloseOffsets.push(m.index);
    events.push({
      type: 'close',
      name: normalizeName(m[1]),
      rawName: m[1],
      offset: m.index,
      length: m[0].length,
    });
  }

  RE_M_CLOSE_BARE.lastIndex = 0;
  while ((m = RE_M_CLOSE_BARE.exec(text)) !== null) {
    if (namedCloseOffsets.includes(m.index)) continue;
    events.push({
      type: 'close',
      name: null,
      rawName: null,
      offset: m.index,
      length: m[0].length,
    });
  }

  RE_M_SELF.lastIndex = 0;
  while ((m = RE_M_SELF.exec(text)) !== null) {
    events.push({
      type: 'self',
      name: normalizeName(m[1]),
      rawName: m[1],
      offset: m.index,
      length: m[0].length,
    });
  }

  events.sort((a, b) => a.offset - b.offset);
  return events;
}

/**
 * Rule 1 & 4: Unclosed opens and orphaned closes.
 */
function checkTagBalance(events, lineStarts, diagnostics) {
  checkBalance(events, diagnostics, {
    severityError: vscode.DiagnosticSeverity.Error,
    diagnostic(offset, length, message, severity) {
      const { line, col } = offsetToLineCol(lineStarts, offset);
      return makeDiag(line, col, col + length, message, severity);
    },
    openTagText: (name) => `<|open|>${name}<|close|>`,
    closeTagText: (name) => `<|open_end|>${name}<|close|>`,
    bareCloseTagText: () => '<|open_end|>',
  });
}

/**
 * Rule 2: Name ending with <|:|> (empty colon label).
 * e.g. <|open|>instr<|:|><|close|>
 */
function checkEmptyColon(text, lineStarts, diagnostics) {
  // A name ends with <|:|> when the colon token is immediately followed by <|close|> or <|self_close|>
  // The regexes above capture that.
  const checks = [
    { re: RE_EMPTY_COLON_OPEN,  label: 'opening' },
    { re: RE_EMPTY_COLON_CLOSE, label: 'closing' },
    { re: RE_EMPTY_COLON_SELF,  label: 'self-closing' },
  ];
  for (const { re, label } of checks) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      const { line, col } = offsetToLineCol(lineStarts, m.index);
      diagnostics.push(makeDiag(
        line, col, col + m[0].length,
        `Tag name cannot end with '<|:|>' — add a label after the colon or remove it`,
        vscode.DiagnosticSeverity.Error
      ));
    }
  }
}

/**
 * Rule 3: Indentation — check actual vs. expected indent depth.
 */
function checkIndentation(text, lineStarts, diagnostics) {
  const lines = text.split('\n');

  const detectedUnit = detectIndentUnit(lines);
  const indentUnit = detectedUnit || '  '; // no indented lines — default, won't fire any warnings
  const unitLen = indentUnit.length;

  const RE_OPEN_G        = /<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g;
  const RE_CLOSE_G       = /<\|open_end\|>((?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g;
  const RE_LEADING_CLOSE = /^<\|open_end\|>((?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/;
  const BARE_CLOSE_TOKEN = '<|open_end|>';

  const trimmedLines = lines.map(l => l.trimStart());
  const depths = resolveIndentDepths(trimmedLines, {
    matchLeadingClose(trimmed) {
      const m = RE_LEADING_CLOSE.exec(trimmed);
      RE_LEADING_CLOSE.lastIndex = 0;
      if (m) return { name: m[1] };
      if (trimmed.startsWith(BARE_CLOSE_TOKEN)) return { name: null };
      return null;
    },
    lineEvents(trimmed) {
      const events = [];
      for (const m of trimmed.matchAll(RE_OPEN_G)) events.push({ pos: m.index, type: 'open', name: m[1] });
      RE_OPEN_G.lastIndex = 0;
      const namedCloseOffsets = [];
      for (const m of trimmed.matchAll(RE_CLOSE_G)) {
        namedCloseOffsets.push(m.index);
        events.push({ pos: m.index, type: 'close', name: m[1] });
      }
      RE_CLOSE_G.lastIndex = 0;
      let bareIdx = trimmed.indexOf(BARE_CLOSE_TOKEN);
      while (bareIdx !== -1) {
        if (!namedCloseOffsets.includes(bareIdx)) {
          events.push({ pos: bareIdx, type: 'close', name: null });
        }
        bareIdx = trimmed.indexOf(BARE_CLOSE_TOKEN, bareIdx + 1);
      }
      return events;
    },
  });

  for (const { lineIndex, depth: lineDepth } of depths) {
    const raw = lines[lineIndex];
    const trimmed = trimmedLines[lineIndex];
    const actualIndent = raw.length - trimmed.length;

    const expectedIndent = lineDepth * unitLen;
    if (actualIndent !== expectedIndent) {
      diagnostics.push(makeDiag(
        lineIndex, 0, actualIndent || trimmed.length,
        `Incorrect indentation: expected ${expectedIndent} spaces (depth ${lineDepth}), found ${actualIndent}`,
        vscode.DiagnosticSeverity.Warning
      ));
    }
  }
}

/**
 * Rule 5: Tag name must not contain raw tag delimiters.
 * In LLMON, structural tokens like <|open|> inside a tag name are invalid.
 * We look for nested <|open|> or <|open_end|> inside a tag name span.
 */
function checkNestedDelimiters(text, lineStarts, diagnostics) {
  // A tag name must not itself contain <|open|> or <|open_end|>.
  // Our regex for names already prevents this via the negative lookahead,
  // but we check for malformed sequences: <|open|><|open|>name<|close|>
  // Bare closing tags (token-saving form) may legitimately sit directly
  // adjacent to the next tag (open, open_end, or another bare close) with
  // no separator, so only a literal <|open|> immediately followed by
  // another <|open(?:_end)?|> is flagged — <|open_end|> followed by
  // anything is never malformed on its own.
  const RE_DOUBLE_OPEN = /<\|open\|><\|open(?:_end)?\|>/g;
  let m;
  RE_DOUBLE_OPEN.lastIndex = 0;
  while ((m = RE_DOUBLE_OPEN.exec(text)) !== null) {
    const { line, col } = offsetToLineCol(lineStarts, m.index);
    diagnostics.push(makeDiag(
      line, col, col + m[0].length,
      `Malformed tag: structural token cannot appear inside another structural token`,
      vscode.DiagnosticSeverity.Error
    ));
  }
}

function makeDiag(line, colStart, colEnd, message, severity) {
  const range = new vscode.Range(line, colStart, line, colEnd);
  const diag = new vscode.Diagnostic(range, message, severity);
  diag.source = 'llmon';
  return diag;
}

/**
 * Run all diagnostics on an LLMON document and publish them.
 */
function updateLlmonDiagnostics(document, collection) {
  if (document.languageId !== 'llmon') return;

  const text = document.getText();
  const lineStarts = buildLineIndex(text);
  const diagnostics = [];

  const events = collectTagEvents(text);
  checkTagBalance(events, lineStarts, diagnostics);
  checkEmptyColon(text, lineStarts, diagnostics);
  checkIndentation(text, lineStarts, diagnostics);
  checkNestedDelimiters(text, lineStarts, diagnostics);

  collection.set(document.uri, diagnostics);
}

module.exports = { updateLlmonDiagnostics };
