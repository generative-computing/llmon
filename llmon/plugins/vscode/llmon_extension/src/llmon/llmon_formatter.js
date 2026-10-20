// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { resolveIndentDepths } = require('../shared/tag_matching');

/**
 * Stack-based indentation formatter for LLMON.
 *
 * Algorithm:
 *   - Track a stack of open tag names.
 *   - When a line's FIRST structural token is <|open_end|>name<|close|>:
 *       - Search the stack for the most-recent matching opener.
 *       - If found: indent this line at that opener's depth, then pop stack down to it.
 *       - If NOT found (orphaned close): indent this line at current depth (no dedent).
 *   - After placing the line, push any net-unclosed openers from this line onto the
 *     stack and increment depth accordingly.
 *   - Self-closing tags and inline open+close pairs (same name, same line) don't
 *     change depth.
 */

// The name group's negative lookahead excludes open|/open_end| in addition to
// close|/self_close| — otherwise a bare <|open_end|> later in the document
// gets absorbed as if it were part of this tag's name, hiding a legitimate
// bare-close match.
const RE_OPEN_G       = /<\|open\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g;
const RE_CLOSE_G      = /<\|open_end\|>((?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/g;
const RE_LEADING_CLOSE = /^<\|open_end\|>((?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|close\|>/;
const BARE_CLOSE_TOKEN = '<|open_end|>';

// Matches any structural LLMON tag token: <|open|>...<|close|>,
// <|open_end|>...<|close|>, <|open|>...<|self_close|>, or a bare
// <|open_end|> (token-saving form with no name/close). The bare-close
// alternative is tried first so it wins over the named-close alternative
// when a bare <|open_end|> is immediately followed by an unrelated tag.
const RE_ANY_TAG_LLMON = /<\|open_end\|>(?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?<\|close\|>|<\|open\|>(?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?<\|self_close\|>|<\|open\|>(?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?<\|close\|>|<\|open_end\|>/g;

function stripTrailingInvisible(s) {
  // Strip trailing whitespace and U+200B zero-width spaces
  return s.replace(/[\t ​]+$/, '');
}

/**
 * Split a single text line into logical LLMON lines so that each structural
 * tag token starts on its own line. Content between tags is placed on its own
 * line as well.
 */
function splitLlmonLine(text) {
  const trimmed = text.trim();
  if (trimmed === '') return [''];

  RE_ANY_TAG_LLMON.lastIndex = 0;
  if (!RE_ANY_TAG_LLMON.test(trimmed)) {
    RE_ANY_TAG_LLMON.lastIndex = 0;
    return [trimmed];
  }
  RE_ANY_TAG_LLMON.lastIndex = 0;

  const tokens = [];
  const re = new RegExp(RE_ANY_TAG_LLMON.source, 'g');
  let m;
  while ((m = re.exec(trimmed)) !== null) {
    tokens.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
  }

  const segments = [];
  let pos = 0;

  for (const tok of tokens) {
    if (tok.start > pos) {
      const before = trimmed.slice(pos, tok.start).trim();
      if (before) segments.push(before);
    }
    segments.push(tok.text);
    pos = tok.end;
  }

  if (pos < trimmed.length) {
    const after = trimmed.slice(pos).trim();
    if (after) segments.push(after);
  }

  return segments.length ? segments : [trimmed];
}

/**
 * Expand a document's lines into logical lines by splitting any line that
 * packs multiple LLMON tag tokens inline.
 */
function expandLines(doc) {
  const lines = [];
  for (let i = 0; i < doc.lineCount; i++) {
    const raw = doc.lineAt(i).text;
    const expanded = splitLlmonLine(raw);
    for (const seg of expanded) lines.push(seg);
  }
  return lines;
}

function computeReindented(doc, options) {
  const indentUnit = options.insertSpaces ? ' '.repeat(options.tabSize) : '\t';

  const logicalLines = expandLines(doc);
  const trimmedLines = logicalLines.map(raw => stripTrailingInvisible(raw.trimStart()));

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

  const depthByLine = new Map(depths.map(d => [d.lineIndex, d.depth]));
  const result = trimmedLines.map((trimmed, i) => {
    if (trimmed === '') return '';
    return indentUnit.repeat(depthByLine.get(i)) + trimmed;
  });

  return result;
}

const llmonFormattingProvider = {
  provideDocumentFormattingEdits(doc, options) {
    const reindented = computeReindented(doc, options);
    const formatted = reindented.join('\n');
    const original  = doc.getText();
    if (original === formatted) return [];
    const fullRange = new vscode.Range(
      doc.positionAt(0),
      doc.positionAt(original.length)
    );
    return [vscode.TextEdit.replace(fullRange, formatted)];
  },

  provideDocumentRangeFormattingEdits(doc, range, options) {
    // Reformat the full document (stack must be computed from the top) and
    // replace the whole content so line-count changes are handled correctly.
    const reindented = computeReindented(doc, options);
    const formatted = reindented.join('\n');
    const original  = doc.getText();
    if (original === formatted) return [];
    const fullRange = new vscode.Range(
      doc.positionAt(0),
      doc.positionAt(original.length)
    );
    return [vscode.TextEdit.replace(fullRange, formatted)];
  }
};

/**
 * Compact an LLMON document: collapse all whitespace between structural tokens
 * and content, removing indentation and inter-tag newlines while preserving
 * content text (including internal spaces) and tag names.
 */
function compactLlmon(text) {
  // Matches any structural LLMON tag token, including a bare <|open_end|>
  // (token-saving form with no name/close).
  const RE_TAG = /<\|open_end\|>(?:<\|(?!close\||open\||open_end\|)[^|]+\|>|[^<])+?<\|close\|>|<\|open\|>(?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?<\|self_close\|>|<\|open\|>(?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?<\|close\|>|<\|open_end\|>/g;

  const parts = [];
  let pos = 0;
  let m;

  while ((m = RE_TAG.exec(text)) !== null) {
    if (m.index > pos) {
      const content = text.slice(pos, m.index);
      // Collapse internal whitespace so embedded newlines don't survive
      if (/\S/.test(content)) {
        parts.push(content.trim().replace(/\s+/g, ' '));
      }
    }
    parts.push(m[0]);
    pos = m.index + m[0].length;
  }

  if (pos < text.length) {
    const trailing = text.slice(pos);
    if (/\S/.test(trailing)) {
      parts.push(trailing.trim().replace(/\s+/g, ' '));
    }
  }

  return parts.join('');
}

module.exports = { llmonFormattingProvider, compactLlmon };
