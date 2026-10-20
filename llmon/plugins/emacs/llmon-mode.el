;;; llmon-mode.el --- Major mode for LLMON -*- lexical-binding: t; -*-

;; Copyright IBM Corp. All Rights Reserved.
;; SPDX-License-Identifier: Apache-2.0

;; Author: LLMON Team
;; Version: 0.1.0
;; Keywords: languages
;; Package-Requires: ((emacs "27.1"))

;; Licensed under the Apache License, Version 2.0.

;;; Commentary:

;; Major mode for editing LLMON files (extension .llmon).
;; LLMON is the LLM-facing syntax of the language, which uses special
;; tokens like <|open|>, <|close|>, <|open_end|>, etc.
;;
;; Features:
;; - Syntax highlighting for special tokens and tag names
;; - Distinct faces for built-in, type, semantic, and user-defined tags
;; - Indentation based on <|open|>/<|open_end|> nesting
;; - Navigation between matching open/close tags
;;
;; Known limitation:
;; Tag matching and navigation (`llmon-find-matching-tag', `llmon-forward-sexp'
;; and the related commands) pair a closing tag with its opening tag by name.
;; They therefore work only when closers carry the tag name, as in
;; <|open_end|>NAME<|close|>.  On the bare <|open_end|> closer, which is the
;; current default, they signal "No matching close tag".  Highlighting is not
;; affected, with one cosmetic exception: a word that immediately follows a
;; bare <|open_end|> is highlighted as a tag name.
;;
;; TODO: match bare closers by counting nesting depth, and stop highlighting
;; plain text after a bare closer as a tag name.
;;
;; Usage:
;;   (require 'llmon-mode)
;;
;; Files with .llmon extension are automatically opened in llmon-mode.

;;; Code:

(require 'align)

;;; Customization

(defgroup llmon nil
  "Major mode for editing LLMON files."
  :group 'languages
  :prefix "llmon-")

(defcustom llmon-indent-offset 2
  "Number of spaces for each indentation level in LLMON."
  :type 'integer
  :group 'llmon)

;;; Faces

(defgroup llmon-faces nil
  "Faces for LLMON mode."
  :group 'llmon
  :group 'faces)

(defface llmon-special-token-face
  '((t :inherit font-lock-bracket-face))
  "Face for special tokens (<|open|>, <|close|>, etc.)."
  :group 'llmon-faces)

(defface llmon-builtin-tag-face
  '((t :inherit font-lock-builtin-face))
  "Face for built-in structural tag names (object, list, item)."
  :group 'llmon-faces)

(defface llmon-type-tag-face
  '((t :inherit font-lock-type-face))
  "Face for primitive type tag names (int, float, bool, none)."
  :group 'llmon-faces)

(defface llmon-keyword-tag-face
  '((t :inherit font-lock-keyword-face))
  "Face for semantic tag names (instr, exec, data, input, output)."
  :group 'llmon-faces)

(defface llmon-user-tag-face
  '((t :inherit font-lock-function-name-face))
  "Face for user-defined tag names."
  :group 'llmon-faces)

(defface llmon-separator-face
  '((t :inherit font-lock-punctuation-face))
  "Face for separator tokens (<|:|>, <|.|>, <|,|>)."
  :group 'llmon-faces)

(defface llmon-constant-face
  '((t :inherit font-lock-constant-face))
  "Face for literal values (true, false, null)."
  :group 'llmon-faces)

;;; Syntax highlighting

(defconst llmon-special-tokens-re
  "<|\\(?:open\\|open_end\\|close\\|self_close\\)|>"
  "Regexp matching LLMON structural special tokens.")

(defconst llmon-separator-tokens-re
  "<|\\(?::\\|[.,]\\||list-separator\\)|>"
  "Regexp matching LLMON separator tokens.")

(defconst llmon-builtin-names '("object" "list" "item" "object_item")
  "Built-in structural tag names in LLMON.")

(defconst llmon-type-names '("int" "float" "bool" "none")
  "Primitive type tag names in LLMON.")

(defconst llmon-keyword-names '("instr" "instruction" "exec" "execution" "data" "input" "output")
  "Semantic tag names in LLMON.")

(defconst llmon-font-lock-keywords
  (let ((builtin-re (regexp-opt llmon-builtin-names 'words))
        (type-re (regexp-opt llmon-type-names 'words))
        (keyword-re (regexp-opt llmon-keyword-names 'words)))
    `(
      ;; Special structural tokens: <|open|>, <|close|>, <|open_end|>, <|self_close|>
      (,(concat "<|\\(open_end\\|open\\|self_close\\|close\\)|>")
       (0 'llmon-special-token-face))

      ;; Separator tokens: <|:|>, <|.|>, <|,|>, <|list-separator|>
      (,(concat "<|\\([:.,-]\\|list-separator\\)|>")
       (0 'llmon-separator-face))

      ;; Built-in tag names between special tokens
      (,(concat "<|\\(?:open_end\\|open\\)|>" "\\(" builtin-re "\\)")
       (1 'llmon-builtin-tag-face))

      ;; Type tag names between special tokens
      (,(concat "<|\\(?:open_end\\|open\\)|>" "\\(" type-re "\\)")
       (1 'llmon-type-tag-face))

      ;; Keyword/semantic tag names between special tokens
      (,(concat "<|\\(?:open_end\\|open\\)|>" "\\(" keyword-re "\\)")
       (1 'llmon-keyword-tag-face))

      ;; User-defined tag names after open/open_end tokens
      (,(concat "<|\\(?:open_end\\|open\\)|>" "\\([_a-zA-Z][_a-zA-Z0-9]*\\)")
       (1 'llmon-user-tag-face))

      ;; Constants
      ("\\<\\(true\\|false\\|null\\)\\>" (1 'llmon-constant-face))))
  "Font-lock keywords for LLMON mode.")

;;; Indentation

(defun llmon--count-opens (line)
  "Count net open tags on LINE (<|open|> minus <|open_end|>).
Self-closing tags (<|open|>...<|self_close|>) do not contribute."
  (let ((opens 0)
        (pos 0))
    ;; Count <|open|> occurrences
    (while (string-match "<|open|>" line pos)
      (setq opens (1+ opens))
      (setq pos (match-end 0)))
    ;; Subtract <|open_end|> occurrences
    (setq pos 0)
    (while (string-match "<|open_end|>" line pos)
      (setq opens (1- opens))
      (setq pos (match-end 0)))
    ;; Subtract self-close (they pair with an <|open|>)
    (setq pos 0)
    (while (string-match "<|self_close|>" line pos)
      (setq opens (1- opens))
      (setq pos (match-end 0)))
    opens))

(defun llmon--line-starts-with-close-p ()
  "Return non-nil if current line starts with <|open_end|>."
  (save-excursion
    (back-to-indentation)
    (looking-at "<|open_end|>")))

(defun llmon-indent-line ()
  "Indent current line in LLMON mode."
  (interactive)
  (let ((indent 0)
        (prev-indent 0)
        (prev-net 0))
    (save-excursion
      (forward-line -1)
      (while (and (not (bobp))
                  (looking-at-p "^[ \t]*$"))
        (forward-line -1))
      (unless (bobp)
        (setq prev-indent (current-indentation))
        (setq prev-net (llmon--count-opens
                        (buffer-substring-no-properties
                         (line-beginning-position)
                         (line-end-position))))))
    (setq indent (max 0 (+ prev-indent (* prev-net llmon-indent-offset))))
    (when (llmon--line-starts-with-close-p)
      (setq indent (max 0 (- indent llmon-indent-offset))))
    (indent-line-to indent)))

;;; Navigation

(defconst llmon--open-tag-re "<|open|>\\([^<]+\\)<|close|>"
  "Regexp matching an LLMON open tag.  Group 1 is the tag name.")

(defconst llmon--close-tag-re "<|open_end|>\\([^<]+\\)<|close|>"
  "Regexp matching an LLMON close tag.  Group 1 is the tag name.")

(defconst llmon--self-close-tag-re "<|open|>\\([^<]+\\)<|self_close|>"
  "Regexp matching an LLMON self-closing tag.  Group 1 is the tag name.")

(defconst llmon--any-tag-re
  (concat "\\(?:<|open_end|>\\([^<]+\\)<|close|>\\)"       ; close (group 1)
          "\\|\\(?:<|open|>\\([^<]+\\)<|self_close|>\\)"   ; self-close (group 2)
          "\\|\\(?:<|open|>\\([^<]+\\)<|close|>\\)")       ; open (group 3)
  "Regexp matching any LLMON tag.  Groups: 1=close, 2=self-close, 3=open.")

(defun llmon-find-matching-tag ()
  "Jump to the matching open or close tag at point.
If point is on <|open|>TAG<|close|>, jump to the corresponding
<|open_end|>TAG<|close|>, and vice versa."
  (interactive)
  (cond
   ;; On an open tag: <|open|>tagname<|close|>
   ((looking-at llmon--open-tag-re)
    (let ((tag-name (match-string 1))
          (depth 1))
      (goto-char (match-end 0))
      (while (and (> depth 0)
                  (re-search-forward
                   (concat "<|\\(open_end\\|open\\)|>"
                           (regexp-quote tag-name)
                           "<|\\(?:close\\|self_close\\)|>")
                   nil t))
        (if (string= (match-string 1) "open")
            (setq depth (1+ depth))
          (setq depth (1- depth))))
      (when (= depth 0)
        (goto-char (match-beginning 0)))))
   ;; On a close tag: <|open_end|>tagname<|close|>
   ((looking-at llmon--close-tag-re)
    (let ((tag-name (match-string 1))
          (depth 1))
      (while (and (> depth 0)
                  (re-search-backward
                   (concat "<|\\(open_end\\|open\\)|>"
                           (regexp-quote tag-name)
                           "<|\\(?:close\\|self_close\\)|>")
                   nil t))
        (if (string= (match-string 1) "open_end")
            (setq depth (1+ depth))
          (setq depth (1- depth))))))))

(defun llmon-forward-sexp (&optional arg)
  "Move forward across one LLMON sexp (tag pair or self-closing tag).
With ARG, do it that many times.  Negative ARG means move backward."
  (interactive "^p")
  (or arg (setq arg 1))
  (if (< arg 0)
      (llmon-backward-sexp (- arg))
    (let ((case-fold-search nil))
      (dotimes (_ arg)
        (cond
         ;; On an open tag: skip to end of matching close tag
         ((looking-at llmon--open-tag-re)
          (let ((tag-name (match-string 1))
                (depth 1))
            (goto-char (match-end 0))
            (while (and (> depth 0)
                        (re-search-forward llmon--any-tag-re nil t))
              (cond
               ((and (match-string 3)
                     (string= (match-string 3) tag-name))
                (setq depth (1+ depth)))
               ((and (match-string 1)
                     (string= (match-string 1) tag-name))
                (setq depth (1- depth)))))
            (when (> depth 0)
              (user-error "No matching close tag for %s" tag-name))))
         ;; On a self-closing tag: skip past it
         ((looking-at llmon--self-close-tag-re)
          (goto-char (match-end 0)))
         ;; On a close tag: skip past it
         ((looking-at llmon--close-tag-re)
          (goto-char (match-end 0)))
         ;; Otherwise: move forward to next tag and process it
         (t
          (if (re-search-forward
               (concat "\\(?:" llmon--open-tag-re
                       "\\|" llmon--self-close-tag-re
                       "\\|" llmon--close-tag-re "\\)")
               nil t)
              (progn
                (goto-char (match-beginning 0))
                (llmon-forward-sexp 1))
            (user-error "No more tags"))))))))

(defun llmon-backward-sexp (&optional arg)
  "Move backward across one LLMON sexp (tag pair or self-closing tag).
With ARG, do it that many times.  Negative ARG means move forward."
  (interactive "^p")
  (or arg (setq arg 1))
  (if (< arg 0)
      (llmon-forward-sexp (- arg))
    (let ((case-fold-search nil))
      (dotimes (_ arg)
        (let ((orig (point)))
          (cond
           ;; Just after a close tag
           ((save-excursion
              (and (re-search-backward llmon--close-tag-re nil t)
                   (= (match-end 0) orig)))
            (goto-char (match-beginning 0))
            (let ((tag-name (match-string 1))
                  (depth 1))
              (while (and (> depth 0)
                          (re-search-backward llmon--any-tag-re nil t))
                (cond
                 ((and (match-string 1)
                       (string= (match-string 1) tag-name))
                  (setq depth (1+ depth)))
                 ((and (match-string 3)
                       (string= (match-string 3) tag-name))
                  (setq depth (1- depth)))))
              (when (> depth 0)
                (goto-char orig)
                (user-error "No matching open tag for %s" tag-name))))
           ;; Just after a self-closing tag
           ((save-excursion
              (and (re-search-backward llmon--self-close-tag-re nil t)
                   (= (match-end 0) orig)))
            (goto-char (match-beginning 0)))
           ;; Just after an open tag
           ((save-excursion
              (and (re-search-backward llmon--open-tag-re nil t)
                   (= (match-end 0) orig)))
            (goto-char (match-beginning 0)))
           ;; Otherwise: search backward for previous tag end
           (t
            (if (re-search-backward
                 (concat "\\(?:" llmon--close-tag-re
                         "\\|" llmon--self-close-tag-re
                         "\\|" llmon--open-tag-re "\\)")
                 nil t)
                (progn
                  (goto-char (match-end 0))
                  (llmon-backward-sexp 1))
              (user-error "No more tags")))))))))

(defun llmon-backward-up-tag (&optional arg)
  "Move backward out of one level of tag nesting.
With ARG, do this that many times."
  (interactive "^p")
  (or arg (setq arg 1))
  (let ((case-fold-search nil))
    (dotimes (_ arg)
      (let ((depth 1)
            (start (point)))
        (while (and (> depth 0)
                    (re-search-backward llmon--any-tag-re nil t))
          (cond
           ((match-string 1) (setq depth (1+ depth)))  ; close tag
           ((match-string 3) (setq depth (1- depth)))  ; open tag
           (t nil)))                                    ; self-close: ignore
        (when (> depth 0)
          (goto-char start)
          (user-error "Already at top level"))))))

(defun llmon-down-tag (&optional arg)
  "Move forward into one level of tag nesting.
With ARG, do this that many times."
  (interactive "^p")
  (or arg (setq arg 1))
  (let ((case-fold-search nil))
    (dotimes (_ arg)
      (let ((start (point)))
        (if (re-search-forward llmon--open-tag-re nil t)
            (goto-char (match-end 0))
          (goto-char start)
          (user-error "No nested tag found"))))))

(defun llmon-up-tag (&optional arg)
  "Move forward out of one level of tag nesting.
With ARG, do this that many times."
  (interactive "^p")
  (or arg (setq arg 1))
  (let ((case-fold-search nil))
    (dotimes (_ arg)
      (let ((depth 1)
            (start (point)))
        (while (and (> depth 0)
                    (re-search-forward llmon--any-tag-re nil t))
          (cond
           ((match-string 3) (setq depth (1+ depth)))  ; open tag
           ((match-string 1) (setq depth (1- depth)))  ; close tag
           (t nil)))                                    ; self-close: ignore
        (when (> depth 0)
          (goto-char start)
          (user-error "Already at top level"))))))

;;; Alignment

(defun llmon-align (beg end)
  "Align `<|:|>' separators in LLMON item lines within BEG to END.
When called interactively with no active region, aligns the
consecutive block of item lines surrounding point."
  (interactive
   (if (use-region-p)
       (list (region-beginning) (region-end))
     (llmon--item-block-bounds)))
  (let ((indent-tabs-mode nil))
    (align-regexp beg end "\\(\\s-*\\)<|:|>" 1 0 nil)))

(defun llmon--item-block-bounds ()
  "Return (BEG END) for the contiguous block of item lines around point."
  (let ((item-re "^[ \t]*<|open|>item<|close|>")
        beg end)
    (save-excursion
      (beginning-of-line)
      (if (not (looking-at item-re))
          (list (point) (point))
        (while (and (not (bobp))
                    (progn (forward-line -1)
                           (looking-at item-re))))
        (unless (looking-at item-re)
          (forward-line 1))
        (setq beg (point))
        (while (and (not (eobp))
                    (looking-at item-re))
          (forward-line 1))
        (setq end (point))
        (list beg end)))))

;;; Syntax table

(defvar llmon-mode-syntax-table
  (let ((st (make-syntax-table)))
    (modify-syntax-entry ?< "." st)
    (modify-syntax-entry ?> "." st)
    (modify-syntax-entry ?| "." st)
    (modify-syntax-entry ?_ "w" st)
    (modify-syntax-entry ?\" "\"" st)
    st)
  "Syntax table for `llmon-mode'.")

;;; Keymap

(defvar llmon-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-c C-f") #'llmon-find-matching-tag)
    (define-key map (kbd "C-c C-a") #'llmon-align)
    (define-key map [remap backward-up-list] #'llmon-backward-up-tag)
    (define-key map [remap down-list] #'llmon-down-tag)
    (define-key map [remap up-list] #'llmon-up-tag)
    map)
  "Keymap for `llmon-mode'.")

;;; Mode definition

;;;###autoload
(define-derived-mode llmon-mode prog-mode "LLMON"
  "Major mode for editing LLMON files.

LLMON uses special tokens like <|open|>, <|close|>,
<|open_end|>, and <|self_close|> to delimit tags.  This mode
provides syntax highlighting and indentation for this format.

\\{llmon-mode-map}"
  :group 'llmon
  :syntax-table llmon-mode-syntax-table
  (setq-local font-lock-defaults '(llmon-font-lock-keywords))
  (setq-local indent-line-function #'llmon-indent-line)
  (setq-local forward-sexp-function #'llmon-forward-sexp)
  (setq-local comment-start nil)
  (setq-local comment-end nil))

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.llmon\\'" . llmon-mode))

(provide 'llmon-mode)
;;; llmon-mode.el ends here
