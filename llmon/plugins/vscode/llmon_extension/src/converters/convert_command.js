// Copyright IBM Corp. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

'use strict';
const vscode = require('vscode');
const { spawn, execFile } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

/*
 * "Convert to View" feature.
 *
 * Runs the (already-installed) `llmon` pip package's console-script converters and captures
 * their stdout. For a SAVED file we pass its path as the converter's input argument so the
 * converter reads the bytes from disk — identical to running the CLI in a terminal. This
 * matters: the converters parse one line at a time, and a reflowed/formatted editor buffer can
 * place a token (e.g. a reference "<|:|>") at a line boundary that the parser can't handle,
 * whereas the on-disk file keeps tokens packed as the package wrote them. (Untitled documents,
 * which have no file on disk, fall back to a temporary file written from the buffer.)
 *
 * Output is written to stdout (the "-" output argument) and held in memory; the result is shown
 * in an untitled editor opened beside the source. The source file is never modified by the
 * conversion itself (we only save it when the user explicitly chooses "Save & Convert" for a
 * file with unsaved edits), and the user decides where (if anywhere) to save the result.
 *
 * The four converters accept a file path or "-" (stdin) for input, and "-" for stdout:
 *   short-to-llmon <in> -     (Shorthand -> LLMON)      flags: -et
 *   llmon-to-short <in> -     (LLMON -> Shorthand)      flags: (none)
 *   llmon-to-json  <in> -     (LLMON -> JSON)           flags: -kai -net -nai
 *   json-to-llmon  <in> -     (JSON  -> LLMON)          flags: -kai -net -nai -compact -noct -noot -ntt
 *
 * The binaries live in the bin/ (POSIX) or Scripts/ (Windows) folder of whatever environment
 * the package was installed into, which may be a virtualenv that is NOT on PATH. Resolution
 * order, lazily and cached per session:
 *   1. The `llmon.converterPath` setting, if the user has pointed us at an install folder.
 *   2. The PATH inherited by the extension host (the common case when VS Code is launched
 *      from a terminal).
 *   3. The user's *login* shell PATH — VS Code launched from the macOS Dock/Finder (or a
 *      Linux desktop launcher) does NOT inherit the shell's PATH, so a Homebrew/pyenv/venv
 *      binary at e.g. /opt/homebrew/bin is invisible to spawn even though `which` finds it in
 *      a terminal. We recover by asking `$SHELL -lic 'command -v <binary>'` for the real path.
 *   4. If even the login shell can't find it, prompt the user to specify the install location.
 */

const CONFIG_SECTION = 'llmon';
const CONFIG_KEY = 'converterPath';

// Per-session cache of resolved absolute binary paths (keyed by command name), so we pay the
// login-shell startup cost at most once per binary. Cleared implicitly on extension reload.
const resolvedBinaryCache = new Map();

// Sentinel thrown by runConversion when the binary couldn't be located (ENOENT on spawn).
class ConverterNotFoundError extends Error {
  constructor(command) {
    super(`Converter binary "${command}" was not found.`);
    this.kind = 'notfound';
    this.command = command;
  }
}

// Shared output channel for full converter error detail (Python tracebacks). Created lazily so
// we don't allocate it unless a conversion actually fails.
let outputChannel = null;
function getOutputChannel() {
  if (!outputChannel) {
    outputChannel = vscode.window.createOutputChannel('LLMON Convert');
  }
  return outputChannel;
}

/**
 * Reduce a converter's stderr (often a multi-line Python traceback) to a single concise reason
 * suitable for a notification toast. Falls back to a generic message if nothing useful is found.
 */
function summarizeStderr(stderr) {
  if (!stderr) return 'The converter rejected this input.';
  const lines = stderr
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0) return 'The converter rejected this input.';

  // The most useful line of a Python traceback is the final exception line, e.g.
  // "AssertionError: expecting <|open_end|>list<|close|>, found <|list-separator|>".
  const last = lines[lines.length - 1];
  const exceptionMatch = last.match(/^[\w.]*(Error|Exception|Warning):\s*(.*)$/);
  if (exceptionMatch) {
    // Strip noisy fully-qualified prefixes (json.decoder.JSONDecodeError -> JSONDecodeError).
    const name = last.slice(0, last.indexOf(':')).split('.').pop();
    return `${name}: ${exceptionMatch[2]}`.trim();
  }

  // The package's own validation errors print a "** ERROR **" banner to stdout/stderr.
  const banner = lines.find((l) => l.includes('** ERROR **'));
  if (banner) return banner.replace(/\*+\s*ERROR\s*\*+/i, '').trim() || banner;

  return last;
}

/**
 * Show a concise, actionable error for a failed conversion, with the full detail (traceback)
 * available via a "Show Details" button that reveals the output channel.
 */
async function reportConversionError(command, args, fullDetail) {
  const reason = summarizeStderr(fullDetail);
  const channel = getOutputChannel();
  channel.appendLine('───────────────────────────────────────────────');
  channel.appendLine(`Command: ${command} ${args.join(' ')} - -`);
  channel.appendLine('');
  channel.appendLine(fullDetail || '(no error output)');
  channel.appendLine('');

  const choice = await vscode.window.showErrorMessage(
    `Conversion failed — the converter rejected this file. ${reason}`,
    'Show Details'
  );
  if (choice === 'Show Details') {
    channel.show(true);
  }
}

/** Resolve a command inside the user-configured install folder, or null if not present there. */
function resolveFromConfiguredPath(command) {
  const configured = vscode.workspace
    .getConfiguration(CONFIG_SECTION)
    .get(CONFIG_KEY, '')
    .trim();
  if (!configured) return null;

  // On Windows the console scripts are <name>.exe (and sometimes .cmd) inside Scripts/.
  const exts = process.platform === 'win32' ? ['.exe', '.cmd', '.bat', ''] : [''];
  for (const ext of exts) {
    const candidate = path.join(configured, command + ext);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  // Configured but missing there — return the join anyway so the error is reported clearly.
  return path.join(configured, command);
}

/**
 * Ask the user's LOGIN shell where a command lives. This recovers the case where VS Code was
 * launched from the Dock/Finder and the extension host inherited only a minimal PATH that
 * omits e.g. /opt/homebrew/bin. Resolves with an absolute path, or null if the shell can't
 * find it (or we're on Windows / there's no usable $SHELL).
 */
function resolveViaLoginShell(command) {
  return new Promise((resolve) => {
    if (process.platform === 'win32') {
      resolve(null);
      return;
    }
    const shell = process.env.SHELL || '/bin/zsh';
    // `command -v` prints the resolved path and exits non-zero if not found.
    // -l (login) + -i (interactive) so the user's PATH-setting rc files are sourced.
    execFile(shell, ['-lic', `command -v ${command}`], { timeout: 8000 }, (err, stdout) => {
      if (err) {
        resolve(null);
        return;
      }
      const found = stdout.toString().trim().split('\n').pop().trim();
      // `command -v` can echo a shell builtin/alias name rather than a path; require a real file.
      if (found && path.isAbsolute(found) && fs.existsSync(found)) {
        resolve(found);
      } else {
        resolve(null);
      }
    });
  });
}

/**
 * Run a converter and resolve with its stdout.
 *
 * `input` selects what the converter reads:
 *   { path: '/abs/file.llmon' } — pass the file path as the input argument so the converter
 *                                  reads the bytes from disk directly (matches running the CLI
 *                                  in a terminal). This is the default for saved files.
 *   { stdinText: '...' }         — pipe text via stdin using the "-" input argument (used as a
 *                                  fallback for untitled documents that have no file on disk).
 *
 * Rejects with a ConverterNotFoundError if the binary can't be located, or a generic Error
 * (carrying stderr) for any non-zero exit.
 *
 * Resolution strategy: try the fast path first (configured path, else the bare command name
 * against the inherited PATH). Only if that spawn fails with ENOENT do we pay for a login-shell
 * lookup and retry, so the common (terminal-launched) case stays fast.
 */
function runConversion(command, args, input) {
  const configured = resolveFromConfiguredPath(command);
  const fastTarget = configured || resolvedBinaryCache.get(command) || command;
  return spawnConverter(fastTarget, command, args, input).catch(async (err) => {
    if (err && err.kind === 'notfound' && !configured) {
      // Dock-launch / venv-not-on-PATH recovery: ask the login shell, then retry once.
      const viaShell = await resolveViaLoginShell(command);
      if (viaShell) {
        resolvedBinaryCache.set(command, viaShell);
        return spawnConverter(viaShell, command, args, input);
      }
    }
    throw err;
  });
}

/** Spawn a single converter attempt against a concrete target path/name. */
function spawnConverter(target, command, args, input) {
  return new Promise((resolve, reject) => {
    // Input argument is the file path (read from disk) or "-" (read from stdin).
    const inputArg = input && input.path ? input.path : '-';
    let child;
    try {
      child = spawn(target, [...args, inputArg, '-'], { shell: false });
    } catch (err) {
      reject(new ConverterNotFoundError(command));
      return;
    }

    let stdout = '';
    let stderr = '';
    let settled = false;

    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });

    child.on('error', (err) => {
      if (settled) return;
      settled = true;
      if (err && err.code === 'ENOENT') {
        reject(new ConverterNotFoundError(command));
      } else {
        reject(new Error(`Failed to start "${command}": ${err && err.message ? err.message : err}`));
      }
    });

    child.on('close', (code) => {
      if (settled) return;
      settled = true;
      if (code !== 0) {
        const detail = stderr.trim() || `exited with code ${code}`;
        reject(new Error(detail));
        return;
      }
      // Some converters (e.g. json-to-llmon's argument-compatibility check) print an
      // "** ERROR **" banner and exit 0 instead of failing. Treat that as an error so we
      // never open the banner text as if it were converted output.
      if (/\*+\s*ERROR\s*\*+/i.test(stdout) && !stderr) {
        reject(new Error(stdout.trim()));
        return;
      }
      resolve(stdout);
    });

    child.stdin.on('error', () => { /* ignore EPIPE if the child died early */ });
    if (input && typeof input.stdinText === 'string') {
      // Stdin mode (untitled docs): pipe the buffer text in, then close stdin.
      child.stdin.write(input.stdinText);
    }
    // File-path mode reads from disk, so there's nothing to write — just close stdin.
    child.stdin.end();
  });
}

// ── Synchronized-scroll helpers ──────────────────────────────────────────────

/**
 * Registry of active SyncedScrollPair instances so we can dispose stale ones and
 * avoid accumulating listeners on repeated conversions of the same source file.
 * Key: source document URI string, Value: SyncedScrollPair
 */
const activeSyncPairs = new Map();

/**
 * Manages bidirectional, proportional scroll synchronization between a source
 * editor and a result editor opened beside it.
 *
 * Scrolling is mirrored by fraction: (visible-center-line / total-lines).  This
 * is intentional — the two documents differ in line count (e.g. Shorthand vs JSON),
 * so a 1:1 line mapping would drift.  Fraction keeps corresponding content
 * roughly aligned throughout the whole document.
 */
class SyncedScrollPair {
  /**
   * @param {vscode.TextEditor} sourceEditor
   * @param {vscode.TextEditor} resultEditor
   */
  constructor(sourceEditor, resultEditor) {
    this._sourceEditor = sourceEditor;
    this._resultEditor = resultEditor;
    this._busy = false; // re-entrancy guard

    // Listen to visible-range changes on ALL editors; filter to our two.
    this._listener = vscode.window.onDidChangeTextEditorVisibleRanges(event => {
      this._onVisibleRangesChanged(event);
    });
  }

  /** @param {vscode.TextEditorVisibleRangesChangeEvent} event */
  _onVisibleRangesChanged(event) {
    if (this._busy) return;
    if (!event.visibleRanges || event.visibleRanges.length === 0) return;

    const isSource = event.textEditor === this._sourceEditor;
    const isResult = event.textEditor === this._resultEditor;
    if (!isSource && !isResult) return;

    // Check that both editors are still valid (not closed/replaced).
    if (!this._isEditorAlive(this._sourceEditor) || !this._isEditorAlive(this._resultEditor)) {
      this.dispose();
      return;
    }

    const from = isSource ? this._sourceEditor : this._resultEditor;
    const to   = isSource ? this._resultEditor : this._sourceEditor;

    this._syncScroll(from, to);
  }

  /** Mirror the visible-center fraction of `from` into `to`. */
  _syncScroll(from, to) {
    const fromLines = from.document.lineCount;
    const toLines   = to.document.lineCount;
    if (fromLines <= 0 || toLines <= 0) return;

    const ranges = from.visibleRanges;
    if (!ranges || ranges.length === 0) return;

    // Compute the center of the visible range as a fraction [0, 1].
    const firstLine = ranges[0].start.line;
    const lastLine  = ranges[ranges.length - 1].end.line;
    const centerLine = (firstLine + lastLine) / 2;
    const fraction = centerLine / Math.max(fromLines - 1, 1);

    // Map that fraction to the target document.
    const targetLine = Math.round(fraction * Math.max(toLines - 1, 1));
    const targetPos  = new vscode.Position(Math.min(targetLine, toLines - 1), 0);
    const targetRange = new vscode.Range(targetPos, targetPos);

    this._busy = true;
    try {
      to.revealRange(targetRange, vscode.TextEditorRevealType.InCenter);
    } finally {
      // Release the guard after a short tick so the reveal event that VS Code
      // fires in response to revealRange doesn't immediately trigger a loop.
      setTimeout(() => { this._busy = false; }, 50);
    }
  }

  /** VS Code doesn't expose a reliable "editor is closed" API; check via the visible list. */
  _isEditorAlive(editor) {
    return vscode.window.visibleTextEditors.includes(editor);
  }

  dispose() {
    this._listener.dispose();
    // Remove from the registry if we're still the current pair for this source doc.
    const key = this._sourceEditor.document.uri.toString();
    if (activeSyncPairs.get(key) === this) {
      activeSyncPairs.delete(key);
    }
  }
}

/**
 * Open converted content in an untitled editor beside the source, then establish
 * a synchronized-scroll link between the source editor and the result editor.
 *
 * Setting the language id reuses the existing Shorthand/LLMON grammars automatically.
 *
 * @param {string} content
 * @param {string} languageId
 * @param {vscode.TextEditor} sourceEditor  The editor that triggered the conversion.
 */
async function openResultBeside(content, languageId, sourceEditor) {
  // Dispose any previous sync pair that was driven from the same source document
  // (e.g. user converts the same file a second time with different flags).
  const sourceKey = sourceEditor ? sourceEditor.document.uri.toString() : null;
  if (sourceKey && activeSyncPairs.has(sourceKey)) {
    activeSyncPairs.get(sourceKey).dispose();
  }

  const doc = await vscode.workspace.openTextDocument({ content, language: languageId });
  const resultEditor = await vscode.window.showTextDocument(doc, {
    viewColumn: vscode.ViewColumn.Beside,
    preview: false,
  });

  // Wire up synchronized scrolling if we have a live source editor.
  if (sourceEditor && vscode.window.visibleTextEditors.includes(sourceEditor)) {
    const pair = new SyncedScrollPair(sourceEditor, resultEditor);
    activeSyncPairs.set(sourceKey, pair);

    // Also clean up when the result document is closed.
    const closeListener = vscode.workspace.onDidCloseTextDocument(closed => {
      if (closed === doc) {
        pair.dispose();
        closeListener.dispose();
      }
    });
  }
}

/**
 * Prompt the user to point the extension at their llmon install folder and persist it.
 * Returns true if a folder was chosen and saved (so the caller can retry).
 */
async function promptForInstallLocation() {
  const choice = await vscode.window.showErrorMessage(
    'The LLMON pip package wasn\'t found on your PATH. If it\'s installed in a virtualenv, ' +
      'point the extension at the folder containing its converter binaries.',
    'Specify Install Location'
  );
  if (choice !== 'Specify Install Location') {
    return false;
  }

  const picked = await vscode.window.showOpenDialog({
    canSelectFolders: true,
    canSelectFiles: false,
    canSelectMany: false,
    title: 'Select the folder containing the llmon converter binaries (e.g. your venv\'s bin or Scripts folder)',
  });
  if (!picked || picked.length === 0) {
    return false;
  }

  await vscode.workspace
    .getConfiguration(CONFIG_SECTION)
    .update(CONFIG_KEY, picked[0].fsPath, vscode.ConfigurationTarget.Global);
  return true;
}

// Monotonic counter for unique temp-file names (Date.now/Math.random not needed; this is
// process-local and only has to be unique within the session).
let tempCounter = 0;

/**
 * Decide what the converter should read for the given document, returning { input, cleanup }
 * where `input` is suitable for runConversion and `cleanup` removes any temp file afterward.
 * Returns null if the user cancelled (e.g. declined to save / convert a dirty file).
 *
 *   - Saved file: convert its path directly (reads disk bytes — matches running the CLI in a
 *     terminal and avoids the line-by-line parser tripping on a reflowed editor buffer). If the
 *     document has unsaved edits, warn and let the user choose to save first or convert the
 *     on-disk version as-is.
 *   - Untitled document (no file on disk): write the buffer to an OS temp file and convert that.
 */
async function resolveInput(document) {
  if (document.isUntitled) {
    const ext = document.languageId === 'json' ? '.json'
      : document.languageId === 'llmon' ? '.llmon' : '.short';
    const tmpPath = path.join(os.tmpdir(), `llmon-convert-${process.pid}-${tempCounter++}${ext}`);
    await fs.promises.writeFile(tmpPath, document.getText(), 'utf8');
    return {
      input: { path: tmpPath },
      cleanup: () => fs.promises.unlink(tmpPath).catch(() => {}),
    };
  }

  if (document.isDirty) {
    const choice = await vscode.window.showWarningMessage(
      'This file has unsaved changes. The converter reads the version saved on disk.',
      'Save & Convert',
      'Convert Saved Version'
    );
    if (!choice) return null; // dismissed
    if (choice === 'Save & Convert') {
      const saved = await document.save();
      if (!saved) return null; // save failed/cancelled
    }
  }

  return { input: { path: document.uri.fsPath }, cleanup: () => {} };
}

/**
 * Build a command handler that converts the active editor's document and shows it beside.
 *
 * @param {object} opts
 * @param {string} opts.command      console-script name (e.g. "short-to-llmon")
 * @param {string[]} opts.args       flags to pass before the input/output arguments
 * @param {string} opts.outputLang   target language id for the result editor
 * @param {string} opts.title        short label used in the progress notification
 */
function makeHandler({ command, args, outputLang, title }) {
  return async function handler() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showErrorMessage('Open a file in the editor before converting.');
      return;
    }

    const resolved = await resolveInput(editor.document);
    if (!resolved) return; // user cancelled
    const { input, cleanup } = resolved;

    const convert = () =>
      vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title },
        () => runConversion(command, args, input)
      );

    try {
      const output = await convert();
      await openResultBeside(output, outputLang, editor);
    } catch (err) {
      if (err && err.kind === 'notfound') {
        const retry = await promptForInstallLocation();
        if (!retry) return;
        try {
          const output = await convert();
          await openResultBeside(output, outputLang, editor);
        } catch (err2) {
          if (err2 && err2.kind === 'notfound') {
            vscode.window.showErrorMessage(
              `The selected folder doesn't contain "${command}". Please choose the folder that ` +
                'holds the llmon converter binaries (e.g. <venv>/bin or <venv>\\Scripts).'
            );
          } else {
            await reportConversionError(command, args, err2.message);
          }
        }
        return;
      }
      await reportConversionError(command, args, err.message);
    } finally {
      await cleanup();
    }
  };
}

// Maps each contributed command id to its converter configuration.
// Note: json-to-llmon errors if -kai is set without -net (check_arg_compatibility), and the
// LLMON->JSON "keys as items" path is paired with -net for a consistent round trip, so the
// "keysAsItems" presets pass both flags.
const COMMANDS = {
  // Shorthand -> LLMON
  'short.convertToLlmon':           { command: 'short-to-llmon', args: [],       outputLang: 'llmon', title: 'Converting to LLMON…' },
  'short.convertToLlmon.expandTags':{ command: 'short-to-llmon', args: ['-et'],  outputLang: 'llmon', title: 'Converting to LLMON (Expand Tags)…' },

  // LLMON -> Shorthand
  'llmon.convertToShort':           { command: 'llmon-to-short', args: [],       outputLang: 'short', title: 'Converting to Shorthand…' },

  // LLMON -> JSON
  'llmon.convertToJson':                  { command: 'llmon-to-json', args: [],              outputLang: 'json', title: 'Converting to JSON…' },
  'llmon.convertToJson.keysAsItems':      { command: 'llmon-to-json', args: ['-kai', '-net'],outputLang: 'json', title: 'Converting to JSON (Keys as Items)…' },
  'llmon.convertToJson.noExpandTags':     { command: 'llmon-to-json', args: ['-net'],        outputLang: 'json', title: 'Converting to JSON (No Expand Tags)…' },
  'llmon.convertToJson.nameArrayInstances':{ command: 'llmon-to-json', args: ['-nai'],       outputLang: 'json', title: 'Converting to JSON (Name Array Instances)…' },

  // JSON -> LLMON
  'json.convertToLlmon':                  { command: 'json-to-llmon', args: [],              outputLang: 'llmon', title: 'Converting to LLMON…' },
  'json.convertToLlmon.keysAsItems':      { command: 'json-to-llmon', args: ['-kai', '-net'],outputLang: 'llmon', title: 'Converting to LLMON (Keys as Items)…' },
  'json.convertToLlmon.noExpandTags':     { command: 'json-to-llmon', args: ['-net'],        outputLang: 'llmon', title: 'Converting to LLMON (No Expand Tags)…' },
  'json.convertToLlmon.nameArrayInstances':{ command: 'json-to-llmon', args: ['-nai'],       outputLang: 'llmon', title: 'Converting to LLMON (Name Array Instances)…' },
  'json.convertToLlmon.compact':          { command: 'json-to-llmon', args: ['-compact'],    outputLang: 'llmon', title: 'Converting to LLMON (Compact)…' },
  'json.convertToLlmon.noCompositeTag':   { command: 'json-to-llmon', args: ['-noct'],       outputLang: 'llmon', title: 'Converting to LLMON (No Composite Tag)…' },
  'json.convertToLlmon.noOpenEndTag':     { command: 'json-to-llmon', args: ['-noot'],       outputLang: 'llmon', title: 'Converting to LLMON (No Open End Tag)…' },
  'json.convertToLlmon.noTypeTags':       { command: 'json-to-llmon', args: ['-ntt'],        outputLang: 'llmon', title: 'Converting to LLMON (No Type Tags)…' },
};

function registerConvertCommands(context) {
  for (const [id, cfg] of Object.entries(COMMANDS)) {
    context.subscriptions.push(
      vscode.commands.registerCommand(id, makeHandler(cfg))
    );
  }
  // Dispose the output channel on reload (created lazily on first failure).
  context.subscriptions.push({ dispose: () => { if (outputChannel) outputChannel.dispose(); } });
}

module.exports = { registerConvertCommands };
