// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { LEGEND, provider: semanticTokensProvider }            = require('./short/short_semantic_tokens');
const { TAG_PAIR_DECORATION, SCOPE_GUIDE_DECORATION, updateTagPairHighlight } = require('./short/short_tag_pair_highlight');
const { definitionProvider }                                   = require('./short/short_definition_provider');
const { completionProvider }                                   = require('./short/short_completion_provider');
const { hoverProvider }                                        = require('./short/short_hover_provider');
const { registerAutoEdits }                                    = require('./short/short_auto_edit');

const { LEGEND: llmonLegend, provider: llmonSemanticTokensProvider } = require('./llmon/llmon_semantic_tokens');
const { updateLlmonTagPairHighlight }                         = require('./llmon/llmon_tag_pair_highlight');
const { llmonDefinitionProvider }                             = require('./llmon/llmon_definition_provider');
const { llmonCompletionProvider }                             = require('./llmon/llmon_completion_provider');
const { llmonHoverProvider }                                  = require('./llmon/llmon_hover_provider');
const { registerLlmonAutoEdits }                              = require('./llmon/llmon_auto_edit');
const { shortFormattingProvider, compactShort }                = require('./short/short_formatter');
const { llmonFormattingProvider, compactLlmon }              = require('./llmon/llmon_formatter');
const { updateShortDiagnostics }                               = require('./short/short_diagnostics');
const { updateLlmonDiagnostics }                              = require('./llmon/llmon_diagnostics');
const { registerConvertCommands }                              = require('./converters/convert_command');

function activate(context) {
  context.subscriptions.push(TAG_PAIR_DECORATION, SCOPE_GUIDE_DECORATION);

  // ── Diagnostics (error squiggles) ────────────────────────────────────────
  const shortDiagCollection   = vscode.languages.createDiagnosticCollection('short');
  const llmonDiagCollection  = vscode.languages.createDiagnosticCollection('llmon');
  context.subscriptions.push(shortDiagCollection, llmonDiagCollection);

  function refreshDiagnostics(document) {
    if (!document) return;
    updateShortDiagnostics(document, shortDiagCollection);
    updateLlmonDiagnostics(document, llmonDiagCollection);
  }

  // Run on the currently open editor immediately on activation
  if (vscode.window.activeTextEditor) {
    refreshDiagnostics(vscode.window.activeTextEditor.document);
  }

  // Re-run whenever a document is edited
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      refreshDiagnostics(event.document);
    })
  );

  // Re-run when switching active editor
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(editor => {
      if (editor) refreshDiagnostics(editor.document);
    })
  );

  // Run on every already-open document when extension first activates
  for (const doc of vscode.workspace.textDocuments) {
    refreshDiagnostics(doc);
  }

  // Clear diagnostics when a document is closed
  context.subscriptions.push(
    vscode.workspace.onDidCloseTextDocument(document => {
      shortDiagCollection.delete(document.uri);
      llmonDiagCollection.delete(document.uri);
    })
  );

  // ── Shorthand providers ──────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.languages.registerDocumentSemanticTokensProvider(
      { language: 'short' },
      semanticTokensProvider,
      LEGEND
    )
  );

  context.subscriptions.push(
    vscode.languages.registerDefinitionProvider(
      { language: 'short' },
      definitionProvider
    )
  );

  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      { language: 'short' },
      completionProvider,
      '\\'
    )
  );

  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      { language: 'short' },
      hoverProvider
    )
  );

  // ── LLMON providers ─────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.languages.registerDocumentSemanticTokensProvider(
      { language: 'llmon' },
      llmonSemanticTokensProvider,
      llmonLegend
    )
  );

  context.subscriptions.push(
    vscode.languages.registerDefinitionProvider(
      { language: 'llmon' },
      llmonDefinitionProvider
    )
  );

  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      { language: 'llmon' },
      llmonCompletionProvider,
      '|'
    )
  );

  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      { language: 'llmon' },
      llmonHoverProvider
    )
  );

  // ── Shared command (go-to-definition from hover link) ────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('llmon.goToDefinitionFromHover', ({ uri, line, character, tagLength }) => {
      const targetUri = vscode.Uri.parse(uri);
      const targetPos = new vscode.Position(line, character);
      const targetRange = new vscode.Range(targetPos, new vscode.Position(line, character + tagLength));
      vscode.window.showTextDocument(targetUri).then(editor => {
        editor.selection = new vscode.Selection(targetPos, targetPos);
        editor.revealRange(targetRange, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
      });
    })
  );

  // ── Tag-pair highlight — update on cursor move, editor change, document edit ──
  context.subscriptions.push(
    vscode.window.onDidChangeTextEditorSelection(event => {
      updateTagPairHighlight(event.textEditor);
      updateLlmonTagPairHighlight(event.textEditor);
    })
  );
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(editor => {
      updateTagPairHighlight(editor);
      updateLlmonTagPairHighlight(editor);
    })
  );
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      const editor = vscode.window.activeTextEditor;
      if (editor && editor.document === event.document) {
        updateTagPairHighlight(editor);
        updateLlmonTagPairHighlight(editor);
      }
    })
  );

  // ── Formatting providers (document + range) ──────────────────────────────
  context.subscriptions.push(
    vscode.languages.registerDocumentFormattingEditProvider(
      { language: 'short' },
      shortFormattingProvider
    )
  );
  context.subscriptions.push(
    vscode.languages.registerDocumentRangeFormattingEditProvider(
      { language: 'short' },
      shortFormattingProvider
    )
  );
  context.subscriptions.push(
    vscode.languages.registerDocumentFormattingEditProvider(
      { language: 'llmon' },
      llmonFormattingProvider
    )
  );
  context.subscriptions.push(
    vscode.languages.registerDocumentRangeFormattingEditProvider(
      { language: 'llmon' },
      llmonFormattingProvider
    )
  );

  registerAutoEdits(context);
  registerLlmonAutoEdits(context);

  // ── Compact command ──────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('short.compact', () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const doc = editor.document;
      const original = doc.getText();
      const compacted = compactShort(original);
      if (original === compacted) return;
      const fullRange = new vscode.Range(doc.positionAt(0), doc.positionAt(original.length));
      editor.edit(eb => eb.replace(fullRange, compacted));
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('llmon.compact', () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const doc = editor.document;
      const original = doc.getText();
      const compacted = compactLlmon(original);
      if (original === compacted) return;
      const fullRange = new vscode.Range(doc.positionAt(0), doc.positionAt(original.length));
      editor.edit(eb => eb.replace(fullRange, compacted));
    })
  );

  // ── Convert to View (Shorthand ⇄ LLMON ⇄ JSON) ──────────────────────────────
  registerConvertCommands(context);
}

function deactivate() {}

module.exports = { activate, deactivate };
