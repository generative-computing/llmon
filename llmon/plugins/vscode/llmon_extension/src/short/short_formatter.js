// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { resolveIndentDepths } = require('../shared/tag_matching');

/**
 * Stack-based indentation formatter for Shorthand.
 *
 * Algorithm:
 *   - Track a stack of open tag names.
 *   - When a line's FIRST structural token is /name/ (closing tag):
 *       - Search the stack for the most-recent matching opener.
 *       - If found: indent this line at that opener's depth, then pop stack down to it.
 *       - If NOT found (orphaned close): indent this line at current depth (no dedent).
 *   - After placing the line, push any net-unclosed openers (\name\) from this line
 *     onto the stack and increment depth accordingly.
 *   - Self-closing tags (\name/) and inline open+close pairs (same name, same line)
 *     don't change depth.
 */

// Tag name: starts with non-space word char, optionally interior chars (incl. spaces) ending on non-space word char
const TAG_NAME = '[A-Za-z0-9_](?:[A-Za-z0-9._: ]*[A-Za-z0-9._:])?';

// \tagname\ — opening tag
const RE_OPEN_G  = new RegExp(`\\\\(${TAG_NAME})\\\\(?!\\/)`, 'g');
// /tagname/ — closing tag
const RE_CLOSE_G = new RegExp(`\\/(${TAG_NAME})\\/`, 'g');
// Leading closing tag on a trimmed line
const RE_LEADING_CLOSE = new RegExp(`^\\/(${TAG_NAME})\\/`);
// Matches any structural tag token: \name\, /name/, or \name/
const RE_ANY_TAG = new RegExp(`(?:\\\\(${TAG_NAME})\\\\(?!\\/))` +
  `|(?:\\/(${TAG_NAME})\\/)` +
  `|(?:\\\\(${TAG_NAME})\\/)`
);

function stripTrailingInvisible(s) {
  return s.replace(/[\t ​]+$/, '');
}

/**
 * Split a single text line into logical Shorthand lines so that each structural
 * tag token starts on its own line. Content that falls between an opener and
 * its closer is placed on its own line as well.
 *
 * The splitter emits segments greedily left-to-right:
 *   1. Any text before the first tag becomes its own line (if non-empty).
 *   2. Each tag token becomes its own line.
 *   3. Text between consecutive tags becomes its own line (if non-empty after trim).
 *
 * Empty strings are filtered out; the result is an array of trimmed, non-empty
 * logical lines that preserve all original content.
 */
function splitShortLine(text) {
  const trimmed = text.trim();
  if (trimmed === '') return [''];

  // Quick check: if the line contains no tag tokens at all, return as-is.
  RE_ANY_TAG.lastIndex = 0;
  if (!RE_ANY_TAG.test(trimmed)) {
    RE_ANY_TAG.lastIndex = 0;
    return [trimmed];
  }
  RE_ANY_TAG.lastIndex = 0;

  // Collect all tag token spans in order.
  const tokens = [];
  const re = new RegExp(RE_ANY_TAG.source, 'g');
  let m;
  while ((m = re.exec(trimmed)) !== null) {
    tokens.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
  }

  const segments = [];
  let pos = 0;

  for (const tok of tokens) {
    // Text before this tag
    if (tok.start > pos) {
      const before = trimmed.slice(pos, tok.start).trim();
      if (before) segments.push(before);
    }
    segments.push(tok.text);
    pos = tok.end;
  }

  // Text after last tag
  if (pos < trimmed.length) {
    const after = trimmed.slice(pos).trim();
    if (after) segments.push(after);
  }

  return segments.length ? segments : [trimmed];
}

/**
 * Expand a document's lines into logical lines by splitting any line that
 * packs multiple Shorthand tag tokens inline.
 */
function expandLines(doc) {
  const lines = [];
  for (let i = 0; i < doc.lineCount; i++) {
    const raw = doc.lineAt(i).text;
    const expanded = splitShortLine(raw);
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
      return m ? { name: m[1] } : null;
    },
    lineEvents(trimmed) {
      const events = [];
      for (const m of trimmed.matchAll(RE_OPEN_G)) events.push({ pos: m.index, type: 'open', name: m[1] });
      RE_OPEN_G.lastIndex = 0;
      for (const m of trimmed.matchAll(RE_CLOSE_G)) events.push({ pos: m.index, type: 'close', name: m[1] });
      RE_CLOSE_G.lastIndex = 0;
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

const shortFormattingProvider = {
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
 * Compact a Shorthand document: collapse all whitespace between structural tokens
 * and content, removing indentation and inter-tag newlines while preserving
 * content text (including internal spaces) and tag names (which may contain spaces).
 *
 * Strategy:
 *   1. Extract all structural tokens (\name\, /name/, \name/) and content spans
 *      by scanning left-to-right.
 *   2. Trim content segments that are purely whitespace — they are inter-tag gaps.
 *   3. Preserve content that contains non-whitespace.
 *   4. Concatenate everything with no separator.
 */
function compactShort(text) {
  // Matches any structural Shorthand tag token
  const RE_TAG = new RegExp(
    `(?:\\\\(${TAG_NAME})\\\\(?!\\/))` +   // \name\
    `|(?:\\/(${TAG_NAME})\\/)` +            // /name/
    `|(?:\\\\(${TAG_NAME})\\/)`,            // \name/
    'g'
  );

  const parts = [];
  let pos = 0;
  let m;

  while ((m = RE_TAG.exec(text)) !== null) {
    // Content between last token and this token
    if (m.index > pos) {
      const content = text.slice(pos, m.index);
      // Keep content only if it has non-whitespace characters;
      // collapse internal whitespace so embedded newlines don't survive
      if (/\S/.test(content)) {
        parts.push(content.trim().replace(/\s+/g, ' '));
      }
    }
    parts.push(m[0]);
    pos = m.index + m[0].length;
  }

  // Trailing content after last token
  if (pos < text.length) {
    const trailing = text.slice(pos);
    if (/\S/.test(trailing)) {
      parts.push(trailing.trim().replace(/\s+/g, ' '));
    }
  }

  return parts.join('');
}

module.exports = { shortFormattingProvider, compactShort };
