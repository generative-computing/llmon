// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';

/*
 * Syntax-agnostic stack-matching algorithms shared by the Shorthand and LLMON
 * modules. Each caller supplies its own tokenizer (regexes that find tag
 * events in the source text); this module only knows about abstract events:
 *
 *   { type: 'open'|'close'|'self', name, offset, length }
 *
 * `name` is `null` for a "bare" closing tag (LLMON's token-saving
 * <|open_end|> form with no name) — such an event always matches whatever
 * tag is currently innermost, regardless of name. Shorthand never produces
 * `name: null` events, so the bare-close branches below are simply never
 * taken for Shorthand — this is a pure generalization, not a behavior change.
 */

/**
 * Walk `events` (already sorted in document order) with a stack, reporting
 * orphaned closes and unclosed opens via `diagnostics.push(...)`.
 *
 * @param {Array} events - { type, name, offset, length }
 * @param {Array} diagnostics - destination array; findings are pushed here
 * @param {object} makeDiag - callbacks to build a diagnostic and format tag text
 *   makeDiag.diagnostic(offset, length, message, severity) -> diagnostic object
 *   makeDiag.severityError / makeDiag.severityWarning - severity values
 *   makeDiag.openTagText(name) -> string, e.g. "\\name\\" or "<|open|>name<|close|>"
 *   makeDiag.closeTagText(name) -> string, e.g. "/name/" or "<|open_end|>name<|close|>"
 *   makeDiag.bareCloseTagText() -> string, e.g. "<|open_end|>" (only needed if bare closes are possible)
 */
function checkBalance(events, diagnostics, makeDiag) {
  const stack = [];

  for (const ev of events) {
    if (ev.type === 'self') continue;

    if (ev.type === 'open') {
      stack.push(ev);
    } else if (ev.name === null) {
      // Bare closing tag: closes whatever is currently innermost, unconditionally.
      if (stack.length === 0) {
        diagnostics.push(makeDiag.diagnostic(
          ev.offset, ev.length,
          `Orphaned closing tag: ${makeDiag.bareCloseTagText()} has no matching open tag`,
          makeDiag.severityError
        ));
      } else {
        stack.pop();
      }
    } else {
      let found = -1;
      for (let i = stack.length - 1; i >= 0; i--) {
        if (stack[i].name === ev.name) { found = i; break; }
      }
      if (found === -1) {
        diagnostics.push(makeDiag.diagnostic(
          ev.offset, ev.length,
          `Orphaned closing tag: no matching opening ${makeDiag.openTagText(ev.name)} found`,
          makeDiag.severityError
        ));
      } else {
        for (let i = stack.length - 1; i > found; i--) {
          const unclosed = stack[i];
          diagnostics.push(makeDiag.diagnostic(
            unclosed.offset, unclosed.length,
            `Unclosed tag ${makeDiag.openTagText(unclosed.name)}: closed by ${makeDiag.closeTagText(ev.name)} before it was closed`,
            makeDiag.severityError
          ));
        }
        stack.splice(found);
      }
    }
  }

  for (const unclosed of stack) {
    diagnostics.push(makeDiag.diagnostic(
      unclosed.offset, unclosed.length,
      `Unclosed tag: ${makeDiag.openTagText(unclosed.name)} has no matching closing tag`,
      makeDiag.severityError
    ));
  }
}

/**
 * Detect the dominant indentation unit used in `lines` (leading whitespace
 * amounts, majority-coverage GCD). Returns the unit string (e.g. '  ', '\t').
 * Shared by the indentation diagnostic and the formatter, which must agree
 * on what "correctly indented" means.
 */
function detectIndentUnit(lines) {
  let usesTabs = false;
  const amountCounts = new Map();
  for (const line of lines) {
    const m = line.match(/^(\s+)\S/);
    if (!m) continue;
    if (m[1].includes('\t')) { usesTabs = true; break; }
    const n = m[1].length;
    if (n > 0) amountCounts.set(n, (amountCounts.get(n) || 0) + 1);
  }
  if (usesTabs) return '\t';
  if (amountCounts.size === 0) return null; // caller decides the no-signal default
  const total = [...amountCounts.values()].reduce((a, b) => a + b, 0);
  const byCount = [...amountCounts.entries()].sort((a, b) => b[1] - a[1]);
  let covered = 0;
  const majorAmounts = [];
  for (const [amt, cnt] of byCount) {
    majorAmounts.push(amt);
    covered += cnt;
    if (covered / total >= 0.9) break;
  }
  let g = majorAmounts[0];
  for (let i = 1; i < majorAmounts.length; i++) {
    let a = g, b = majorAmounts[i];
    while (b) { const t = b; b = a % b; a = t; }
    g = a;
  }
  return ' '.repeat(g || 1);
}

/**
 * Walk trimmed source `lines`, tracking a stack of open tag names to
 * determine the expected indent depth of each non-blank line. This is the
 * bookkeeping shared by the indentation diagnostic (which compares depth
 * against actual indent) and the formatter (which uses depth to re-emit
 * lines) — previously two independent copies per syntax (four total).
 *
 * @param {string[]} lines - already logically split (one structural token
 *   or content span per line, as produced by each formatter's expandLines)
 * @param {object} tokenizer
 *   tokenizer.matchLeadingClose(trimmedLine) -> { name } | { name: null } | null
 *     name is null for a bare close, absent match returns null
 *   tokenizer.lineEvents(trimmedLine) -> [{ pos, type: 'open'|'close', name }]
 *     name is null for a bare close event
 * @returns {Array<{ lineIndex, depth }>} one entry per non-blank line, in
 *   the same order as `lines` (blank lines are omitted)
 */
function resolveIndentDepths(lines, tokenizer) {
  const stack = []; // { name, depth }
  let depth = 0;
  const results = [];

  for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
    const trimmed = lines[lineIdx];
    if (trimmed === '') continue;

    let lineDepth = depth;

    const leadingClose = tokenizer.matchLeadingClose(trimmed);
    if (leadingClose) {
      if (leadingClose.name === null) {
        // Bare closing tag: pops the innermost open, unconditionally.
        if (stack.length > 0) {
          const top = stack.pop();
          lineDepth = top.depth;
          depth = lineDepth;
        }
      } else {
        let found = -1;
        for (let j = stack.length - 1; j >= 0; j--) {
          if (stack[j].name === leadingClose.name) { found = j; break; }
        }
        if (found !== -1) {
          lineDepth = stack[found].depth;
          stack.splice(found);
          depth = lineDepth;
        }
        // Orphaned close: stay at current depth, do NOT change depth.
      }
    }

    results.push({ lineIndex: lineIdx, depth: lineDepth });

    const events = tokenizer.lineEvents(trimmed);
    events.sort((a, b) => a.pos - b.pos);

    const localStack = [];
    for (const ev of events) {
      if (ev.type === 'open') {
        localStack.push(ev.name);
      } else if (ev.name === null) {
        localStack.pop();
      } else {
        const idx = localStack.lastIndexOf(ev.name);
        if (idx !== -1) localStack.splice(idx, 1);
      }
    }
    for (const name of localStack) {
      stack.push({ name, depth: lineDepth });
      depth++;
    }
  }

  return results;
}

/**
 * Given `tokens` (sorted by `start`, each { kind: 'open'|'close'|'self',
 * name, start, end }) and a cursor offset, find the paired tag to
 * highlight. Uses a full stack simulation so that a bare close (name:
 * null) correctly pairs with whatever tag is innermost at that point,
 * regardless of its name — a strict generalization of same-name depth
 * counting that produces identical results when no bare closes exist.
 * Returns { start, end } of the partner, or null.
 */
function findPairedTag(tokens, cursorOffset) {
  let cursorToken = null;
  for (const tok of tokens) {
    if (cursorOffset >= tok.start && cursorOffset <= tok.end) {
      cursorToken = tok;
      break;
    }
  }

  if (!cursorToken || cursorToken.kind === 'self') return null;

  const stack = [];
  const partnerOf = new Map();

  for (const tok of tokens) {
    if (tok.kind === 'self') continue;
    if (tok.kind === 'open') {
      stack.push(tok);
    } else {
      let opener = null;
      if (tok.name === null) {
        opener = stack.pop() || null;
      } else {
        for (let i = stack.length - 1; i >= 0; i--) {
          if (stack[i].name === tok.name) { opener = stack[i]; stack.splice(i); break; }
        }
      }
      if (opener) {
        partnerOf.set(opener, tok);
        partnerOf.set(tok, opener);
      }
    }
  }

  const partner = partnerOf.get(cursorToken);
  if (!partner) return null;
  return { start: partner.start, end: partner.end };
}

module.exports = { checkBalance, detectIndentUnit, resolveIndentDepths, findPairedTag };
