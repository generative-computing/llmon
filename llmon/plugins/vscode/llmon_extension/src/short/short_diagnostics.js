// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { checkBalance, detectIndentUnit, resolveIndentDepths } = require('../shared/tag_matching');

// Tag name: starts with non-space word char, optionally interior chars (incl. spaces) ending on non-space word char
const TAG_NAME = '[A-Za-z0-9_](?:[A-Za-z0-9._: ]*[A-Za-z0-9._:])?';

// Opening tag: \name\
const RE_OPEN      = new RegExp(`\\\\(${TAG_NAME})\\\\(?!\\/)`, 'g');
// Closing tag:  /name/
const RE_CLOSE     = new RegExp(`\\/(${TAG_NAME})\\/`, 'g');
// Self-closing: \name/
const RE_SELF      = new RegExp(`(?<![A-Za-z0-9._:\\\\\\/])\\\\(${TAG_NAME})\\/`, 'g');

// Tag name with a trailing colon and nothing after it — e.g. \instr:\  or \foo:\
const RE_EMPTY_COLON = /\\([A-Za-z0-9_][A-Za-z0-9._]*:)(?:\\|\/)/g;

// Any tag-like token: opening, closing, or self-closing (used to scan for nested delimiters)
const RE_ANY_TAG_TOKEN = new RegExp(`(?:\\\\(${TAG_NAME})(?:\\\\(?!\\/)|\\/)|(\\/(${TAG_NAME})\\/))`);

/**
 * Collect all tag events from a document line-by-line, returning:
 *   { type: 'open'|'close'|'self', name, line, colStart, colEnd, offset, length, fullMatch }
 * `offset`/`length` (absolute, whole-document) are included so the shared
 * stack-matching algorithm in ../shared/tag_matching can operate without
 * knowing about line/column coordinates.
 */
function collectTagEvents(text) {
  const lines = text.split('\n');
  const events = [];
  let lineStart = 0;

  for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
    const line = lines[lineIdx];

    RE_OPEN.lastIndex = 0;
    let m;
    while ((m = RE_OPEN.exec(line)) !== null) {
      events.push({
        type: 'open',
        name: m[1],
        line: lineIdx,
        colStart: m.index,
        colEnd: m.index + m[0].length,
        offset: lineStart + m.index,
        length: m[0].length,
        fullMatch: m[0],
      });
    }

    RE_CLOSE.lastIndex = 0;
    while ((m = RE_CLOSE.exec(line)) !== null) {
      events.push({
        type: 'close',
        name: m[1],
        line: lineIdx,
        colStart: m.index,
        colEnd: m.index + m[0].length,
        offset: lineStart + m.index,
        length: m[0].length,
        fullMatch: m[0],
      });
    }

    RE_SELF.lastIndex = 0;
    while ((m = RE_SELF.exec(line)) !== null) {
      events.push({
        type: 'self',
        name: m[1],
        line: lineIdx,
        colStart: m.index,
        colEnd: m.index + m[0].length,
        offset: lineStart + m.index,
        length: m[0].length,
        fullMatch: m[0],
      });
    }

    lineStart += line.length + 1; // +1 for the '\n' split away by text.split('\n')
  }

  // Sort by line then col so we process document order
  events.sort((a, b) => a.line !== b.line ? a.line - b.line : a.colStart - b.colStart);
  return events;
}

/**
 * Rule 1 & 4: Unclosed opens and orphaned closes.
 *
 * Uses a stack: push opens, pop on matching close.
 * After processing all events:
 *   - remaining stack entries → unclosed opens
 *   - closes with no matching open → orphaned closes
 */
function checkTagBalance(events, diagnostics) {
  // The shared algorithm speaks in absolute offsets; map back to the
  // originating event (which carries line/col) to build a vscode.Range.
  const eventByOffset = new Map();
  for (const ev of events) eventByOffset.set(ev.offset, ev);

  checkBalance(events, diagnostics, {
    severityError: vscode.DiagnosticSeverity.Error,
    diagnostic(offset, length, message, severity) {
      const ev = eventByOffset.get(offset);
      return makeDiag(ev.line, ev.colStart, ev.colEnd, message, severity);
    },
    openTagText: (name) => `\\${name}\\`,
    closeTagText: (name) => `/${name}/`,
    bareCloseTagText: () => '', // Shorthand never produces bare-close events
  });
}

/**
 * Rule 2: Tag name ending with a colon and no label after it.
 * e.g. \instr:\ or /instr:/
 */
function checkEmptyColon(text, diagnostics) {
  const lines = text.split('\n');
  // Match open or close tags where the name ends with ':'
  const RE_EMPTY_COLON_OPEN  = /\\([A-Za-z0-9_][A-Za-z0-9._]*:)\\(?!\/)/g;
  const RE_EMPTY_COLON_CLOSE = /\/([A-Za-z0-9_](?:[A-Za-z0-9._: ]*[A-Za-z0-9._:])?:)\//g;
  const RE_EMPTY_COLON_SELF  = /(?<![A-Za-z0-9._:\\\/])\\([A-Za-z0-9_][A-Za-z0-9._]*:)\//g;

  for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
    const line = lines[lineIdx];

    RE_EMPTY_COLON_OPEN.lastIndex = 0;
    let m;
    while ((m = RE_EMPTY_COLON_OPEN.exec(line)) !== null) {
      diagnostics.push(makeDiag(
        lineIdx, m.index, m.index + m[0].length,
        `Tag name cannot end with ':' — add a label after the colon or remove it`,
        vscode.DiagnosticSeverity.Error
      ));
    }

    RE_EMPTY_COLON_CLOSE.lastIndex = 0;
    while ((m = RE_EMPTY_COLON_CLOSE.exec(line)) !== null) {
      diagnostics.push(makeDiag(
        lineIdx, m.index, m.index + m[0].length,
        `Tag name cannot end with ':' — add a label after the colon or remove it`,
        vscode.DiagnosticSeverity.Error
      ));
    }

    RE_EMPTY_COLON_SELF.lastIndex = 0;
    while ((m = RE_EMPTY_COLON_SELF.exec(line)) !== null) {
      diagnostics.push(makeDiag(
        lineIdx, m.index, m.index + m[0].length,
        `Tag name cannot end with ':' — add a label after the colon or remove it`,
        vscode.DiagnosticSeverity.Error
      ));
    }
  }
}

/**
 * Rule 3: Indentation — each open tag must be at depth * indentUnit from the left.
 * Closing tags must align with their matching opener's depth.
 * Self-closing tags must be at the current depth.
 *
 * We compute the expected depth for every event using the same stack as the
 * formatter, then compare against actual leading whitespace.
 */
function checkIndentation(text, diagnostics) {
  const lines = text.split('\n');

  const detectedUnit = detectIndentUnit(lines);
  const indentUnit = detectedUnit || '    '; // no indented lines — default, won't fire any warnings
  const unitLen = indentUnit.length;

  // Reuse formatter's RE patterns
  const RE_OPEN_G        = new RegExp(`\\\\(${TAG_NAME})\\\\(?!\\/)`, 'g');
  const RE_CLOSE_G       = new RegExp(`\\/(${TAG_NAME})\\/`, 'g');
  const RE_LEADING_CLOSE = new RegExp(`^\\/(${TAG_NAME})\\/`);

  const trimmedLines = lines.map(l => l.trimStart());
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


function makeDiag(line, colStart, colEnd, message, severity) {
  const range = new vscode.Range(line, colStart, line, colEnd);
  const diag = new vscode.Diagnostic(range, message, severity);
  diag.source = 'short';
  return diag;
}

/**
 * Run all diagnostics on a Shorthand document and publish them.
 */
function updateShortDiagnostics(document, collection) {
  if (document.languageId !== 'short') return;

  const text = document.getText();
  const diagnostics = [];

  const events = collectTagEvents(text);
  checkTagBalance(events, diagnostics);
  checkEmptyColon(text, diagnostics);
  checkIndentation(text, diagnostics);

  collection.set(document.uri, diagnostics);
}

module.exports = { updateShortDiagnostics };
