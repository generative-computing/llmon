// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { getLlmonReferenceAtPosition, findLlmonOpeningTagOffset } = require('./llmon_definition_provider');

const HOVER_DESCRIPTIONS = {
  instr:       'Instruction tags specify behaviors and directives that an LLM should follow explicitly.',
  instruction: 'Instruction tags specify behaviors and directives that an LLM should follow explicitly.',
  data:        'Data tags contain raw data or values to be consumed or referenced by instructions and execution blocks.',
  exec:        'Execution tags mark blocks that are intended to be executed or run — they define actions, calls, or operations.',
  execution:   'Execution tags mark blocks that are intended to be executed or run — they define actions, calls, or operations.',
  object:      'Object tags represent a structured key-value mapping, similar to a dictionary or JSON object.',
  item:        'Item tags represent a single key-value entry within an object.',
  list:        'List tags represent an ordered sequence of values.',
  int:         'Int tags hold an integer numeric value.',
  float:       'Float tags hold a floating-point numeric value.',
  bool:        'Bool tags hold a boolean value (true or false).',
  none:        'None tags represent an absent or null value.',
  input:       'Input tags mark data provided as input to an instruction or execution block.',
  output:      'Output tags mark data produced as output from an instruction or execution block.',
};

/** Return the base prefix of a built-in word (before any ':label' suffix). */
function getBuiltinPrefix(word) {
  const m = word.match(/^(instruction|instr|data|execution|exec|object|item|list|int|float|bool|none|input|output)(?::[A-Za-z0-9_][A-Za-z0-9._:]*)?$/);
  return m ? m[1] : null;
}

/**
 * Given a line and character position, determine if the cursor is hovering
 * over the name segment of a tag declaration inside a structural token.
 * Returns the tag name (normalized dot/colon form) or null.
 */
function getLlmonTagNameAtPosition(lineText, col) {
  // Match full tag declarations and check if col falls on the name segment.
  const patterns = [
    // <|open|>name<|close|>  or  <|open_end|>name<|close|>  or  <|open|>name<|self_close|>
    /<\|open(?:_end)?\|>((?:<\|(?!close\||self_close\||open\||open_end\|)[^|]+\|>|[^<])+?)<\|(?:close|self_close)\|>/g,
  ];
  for (const re of patterns) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(lineText)) !== null) {
      const fullStart = m.index;
      const fullEnd   = fullStart + m[0].length;
      // Find where the name starts/ends within the match
      const prefixToken = m[0].startsWith('<|open_end|>') ? '<|open_end|>' : '<|open|>';
      const nameStart = fullStart + prefixToken.length;
      const nameEnd   = fullEnd - (m[0].endsWith('<|self_close|>') ? 14 : 9);
      if (col >= nameStart && col <= nameEnd) {
        // Normalize the raw name to dot/colon form
        return m[1].replace(/<\|\.\|>/g, '.').replace(/<\|:\|>/g, ':');
      }
    }
  }
  return null;
}

const llmonHoverProvider = {
  provideHover(document, position) {
    if (document.languageId !== 'llmon') return null;

    const ref      = getLlmonReferenceAtPosition(document, position);
    const lineText = document.lineAt(position.line).text;
    const col      = position.character;

    let word      = null;
    let isTagDecl = false;

    if (ref) {
      word = ref.word;
    } else {
      const tagName = getLlmonTagNameAtPosition(lineText, col);
      if (tagName) {
        word      = tagName;
        isTagDecl = true;
      }
    }

    if (!word) return null;

    const prefix = getBuiltinPrefix(word);
    const parts  = [];

    if (prefix) {
      const desc = HOVER_DESCRIPTIONS[prefix];
      if (desc) parts.push(desc);

      if (word.includes(':')) {
        const label   = word.split(':').slice(1).join(':');
        const tagType = (prefix === 'instr' || prefix === 'instruction') ? 'instruction'
                      : (prefix === 'exec'  || prefix === 'execution')   ? 'execution'
                      : 'data';
        parts.push(`**Label:** \`${label}\`  \n**Type:** ${tagType}`);
      }
    }

    if (!isTagDecl) {
      const text      = document.getText();
      const defOffset = findLlmonOpeningTagOffset(text, word);
      if (defOffset !== -1) {
        const targetPos = document.positionAt(defOffset);
        const args = encodeURIComponent(JSON.stringify({
          uri:       document.uri.toString(),
          line:      targetPos.line,
          character: targetPos.character,
          tagLength: word.length + 8 + 9,   // <|open|> + name + <|close|> approx
        }));
        parts.push(`[Go to definition](command:llmon.goToDefinitionFromHover?${args})`);
      }
    }

    if (parts.length === 0) return null;

    const md = new vscode.MarkdownString(parts.join('\n\n'));
    md.isTrusted = true;
    return new vscode.Hover(md);
  }
};

module.exports = { llmonHoverProvider };
