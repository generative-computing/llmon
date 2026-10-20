;;; short-mode.el --- Major mode for Shorthand, the human-friendly syntax of LLMON -*- lexical-binding: t; -*-

;; Copyright IBM Corp. All Rights Reserved.
;; SPDX-License-Identifier: Apache-2.0

;; Author: LLMON Team
;; Version: 0.1.0
;; Keywords: languages
;; Package-Requires: ((emacs "27.1"))

;; Licensed under the Apache License, Version 2.0.

;;; Commentary:

;; Major mode for editing Shorthand files (extension .short).
;; Shorthand is the human-friendly syntax of LLMON (LLM Object Notation),
;; written with \\tag\\ ... /tag/ markup.
;;
;; Features:
;; - Syntax highlighting for built-in tags (object, list, item, int, float,
;;   bool, none, input, output) and semantic tags (instr, exec, data)
;; - Highlighting for user-defined tags and self-closing tags
;; - Indentation support based on tag nesting
;; - Navigation commands for jumping between matching tags
;;
;; Usage:
;;   (require 'short-mode)
;;
;; Files with .short extension are automatically opened in short-mode.

;;; Code:

(require 'align)

;;; Customization

(defgroup short nil
  "Major mode for editing Shorthand files."
  :group 'languages
  :prefix "short-")

(defcustom short-indent-offset 2
  "Number of spaces for each indentation level in Shorthand."
  :type 'integer
  :group 'short)

;;; Faces

(defgroup short-faces nil
  "Faces for Shorthand mode."
  :group 'short
  :group 'faces)

(defface short-tag-delimiter-face
  '((t :inherit font-lock-bracket-face))
  "Face for tag delimiters (\\\\ and /)."
  :group 'short-faces)

(defface short-builtin-tag-face
  '((t :inherit font-lock-builtin-face))
  "Face for built-in structural tags (object, list, item)."
  :group 'short-faces)

(defface short-type-tag-face
  '((t :inherit font-lock-type-face))
  "Face for primitive type tags (int, float, bool, none)."
  :group 'short-faces)

(defface short-keyword-tag-face
  '((t :inherit font-lock-keyword-face))
  "Face for semantic tags (instr, exec, data, input, output)."
  :group 'short-faces)

(defface short-user-tag-face
  '((t :inherit font-lock-function-name-face))
  "Face for user-defined tags."
  :group 'short-faces)

(defface short-ref-face
  '((t :inherit font-lock-variable-name-face))
  "Face for reference tags (\\ref\\ ... /ref/)."
  :group 'short-faces)

(defface short-separator-face
  '((t :inherit font-lock-punctuation-face))
  "Face for separators (: in items, , in lists)."
  :group 'short-faces)

(defface short-constant-face
  '((t :inherit font-lock-constant-face))
  "Face for literal values (true, false, null) and numbers inside type tags."
  :group 'short-faces)

;;; Syntax highlighting

(defconst short-tag-name-re "[_a-zA-Z][_a-zA-Z0-9.:]*"
  "Regular expression matching a Shorthand tag name.")

(defconst short-builtin-tags-re
  (regexp-opt '("object" "list" "item") 'symbols)
  "Regexp matching built-in structural tag names.")

(defconst short-type-tags-re
  (regexp-opt '("int" "float" "bool" "none") 'symbols)
  "Regexp matching primitive type tag names.")

(defconst short-keyword-tags-re
  (regexp-opt '("instr" "instruction" "exec" "execution" "data" "input" "output") 'symbols)
  "Regexp matching semantic tag names.")

(defconst short-font-lock-keywords
  (let ((tag-id short-tag-name-re))
    `(
      ;; Reference tags: \ref\ content /ref/
      (,(concat "\\(\\\\\\)\\(ref\\)\\(\\\\\\)\\(.*?\\)\\(/\\)\\(ref\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-ref-face)
       (3 'short-tag-delimiter-face)
       (4 'short-ref-face)
       (5 'short-tag-delimiter-face)
       (6 'short-ref-face)
       (7 'short-tag-delimiter-face))

      ;; Self-closing built-in tags: \object/
      (,(concat "\\(\\\\\\)\\(" short-builtin-tags-re "\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-builtin-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Open built-in tags: \object\
      (,(concat "\\(\\\\\\)\\(\\(?:object\\|list\\|item\\)\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(\\\\\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-builtin-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Close built-in tags: /object/
      (,(concat "\\(/\\)\\(\\(?:object\\|list\\|item\\)\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-builtin-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Self-closing type tags: \int/
      (,(concat "\\(\\\\\\)\\(\\(?:int\\|float\\|bool\\|none\\)\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-type-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Open type tags: \int\
      (,(concat "\\(\\\\\\)\\(\\(?:int\\|float\\|bool\\|none\\)\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(\\\\\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-type-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Close type tags: /int/
      (,(concat "\\(/\\)\\(\\(?:int\\|float\\|bool\\|none\\)\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-type-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Self-closing keyword/semantic tags: \instr:a/
      (,(concat "\\(\\\\\\)\\(\\(?:instr\\|instruction\\|exec\\|execution\\|data\\|input\\|output\\)\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-keyword-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Open keyword/semantic tags: \instr:a\
      (,(concat "\\(\\\\\\)\\(\\(?:instr\\|instruction\\|exec\\|execution\\|data\\|input\\|output\\)\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(\\\\\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-keyword-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Close keyword/semantic tags: /instr:a/
      (,(concat "\\(/\\)\\(\\(?:instr\\|instruction\\|exec\\|execution\\|data\\|input\\|output\\)\\(?::[_a-zA-Z0-9.]*\\)?\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-keyword-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Self-closing user tags: \mytag/
      (,(concat "\\(\\\\\\)\\(" tag-id "\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-user-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Open user tags: \mytag\
      (,(concat "\\(\\\\\\)\\(" tag-id "\\)\\(\\\\\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-user-tag-face)
       (3 'short-tag-delimiter-face))

      ;; Close user tags: /mytag/
      (,(concat "\\(/\\)\\(" tag-id "\\)\\(/\\)")
       (1 'short-tag-delimiter-face)
       (2 'short-user-tag-face)
       (3 'short-tag-delimiter-face))

      ;; List separator comma (only highlights commas not inside quoted strings)
      ("," . 'short-separator-face)))
  "Font-lock keywords for Shorthand mode.")

;;; Indentation

(defun short--count-open-tags (line)
  "Count net opening tags on LINE (opens minus closes)."
  (let ((opens 0)
        (pos 0))
    ;; Count open tags: \tagname\  (not self-closing \tagname/)
    (while (string-match "\\\\[_a-zA-Z][_a-zA-Z0-9.:]*\\\\" line pos)
      (setq opens (1+ opens))
      (setq pos (match-end 0)))
    ;; Subtract close tags: /tagname/
    (setq pos 0)
    (while (string-match "/[_a-zA-Z][_a-zA-Z0-9.:]*/" line pos)
      (setq opens (1- opens))
      (setq pos (match-end 0)))
    opens))

(defun short--line-starts-with-close-tag-p ()
  "Return non-nil if current line starts with a closing tag."
  (save-excursion
    (back-to-indentation)
    (looking-at "/[_a-zA-Z][_a-zA-Z0-9.:]*/")))

(defun short-indent-line ()
  "Indent current line in Shorthand mode."
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
        (setq prev-net (short--count-open-tags
                        (buffer-substring-no-properties
                         (line-beginning-position)
                         (line-end-position))))))
    (setq indent (max 0 (+ prev-indent (* prev-net short-indent-offset))))
    (when (short--line-starts-with-close-tag-p)
      (setq indent (max 0 (- indent short-indent-offset))))
    (indent-line-to indent)))

;;; Navigation

(defconst short--open-tag-re "\\\\\\([_a-zA-Z][_a-zA-Z0-9.:]*\\)\\\\"
  "Regexp matching an open tag.  Group 1 is the tag name.")

(defconst short--close-tag-re "/\\([_a-zA-Z][_a-zA-Z0-9.:]*\\)/"
  "Regexp matching a close tag.  Group 1 is the tag name.")

(defconst short--self-close-tag-re "\\\\\\([_a-zA-Z][_a-zA-Z0-9.:]*\\)/"
  "Regexp matching a self-closing tag.  Group 1 is the tag name.")

(defconst short--any-tag-re
  (concat "\\(?:" "\\\\\\([_a-zA-Z][_a-zA-Z0-9.:]*\\)\\\\" "\\)"   ; open  (group 1)
          "\\|\\(?:" "\\\\\\([_a-zA-Z][_a-zA-Z0-9.:]*\\)/" "\\)"   ; self-close (group 2)
          "\\|\\(?:" "/\\([_a-zA-Z][_a-zA-Z0-9.:]*\\)/" "\\)")     ; close (group 3)
  "Regexp matching any tag.  Groups: 1=open, 2=self-close, 3=close.")

(defun short-find-matching-tag ()
  "Jump to the matching open or close tag at point."
  (interactive)
  (let ((case-fold-search nil))
    (cond
     ;; On an open tag \tagname\
     ((looking-at short--open-tag-re)
      (let ((tag-name (match-string 1))
            (depth 1))
        (forward-char)
        (while (and (> depth 0)
                    (re-search-forward
                     (concat "\\(?:\\\\\\(" (regexp-quote tag-name) "\\)\\\\\\|/\\(" (regexp-quote tag-name) "\\)/\\)")
                     nil t))
          (if (match-string 1)
              (setq depth (1+ depth))
            (setq depth (1- depth))))
        (when (= depth 0)
          (goto-char (match-beginning 0)))))
     ;; On a close tag /tagname/
     ((looking-at short--close-tag-re)
      (let ((tag-name (match-string 1))
            (depth 1))
        (while (and (> depth 0)
                    (re-search-backward
                     (concat "\\(?:\\\\\\(" (regexp-quote tag-name) "\\)\\\\\\|/\\(" (regexp-quote tag-name) "\\)/\\)")
                     nil t))
          (if (match-string 2)
              (setq depth (1+ depth))
            (setq depth (1- depth)))))))))

(defun short-forward-sexp (&optional arg)
  "Move forward across one Shorthand sexp (tag pair or self-closing tag).
With ARG, do it that many times.  Negative ARG means move backward."
  (interactive "^p")
  (or arg (setq arg 1))
  (if (< arg 0)
      (short-backward-sexp (- arg))
    (let ((case-fold-search nil))
      (dotimes (_ arg)
        (cond
         ;; On an open tag: skip to end of matching close tag
         ((looking-at short--open-tag-re)
          (let ((tag-name (match-string 1))
                (depth 1))
            (goto-char (match-end 0))
            (while (and (> depth 0)
                        (re-search-forward short--any-tag-re nil t))
              (cond
               ((and (match-string 1)
                     (string= (match-string 1) tag-name))
                (setq depth (1+ depth)))
               ((and (match-string 3)
                     (string= (match-string 3) tag-name))
                (setq depth (1- depth)))))
            (when (> depth 0)
              (user-error "No matching close tag for \\%s\\" tag-name))))
         ;; On a self-closing tag: skip past it
         ((looking-at short--self-close-tag-re)
          (goto-char (match-end 0)))
         ;; On a close tag: skip past it
         ((looking-at short--close-tag-re)
          (goto-char (match-end 0)))
         ;; Otherwise: move forward to next tag and process it
         (t
          (if (re-search-forward
               (concat "\\(?:" short--open-tag-re
                       "\\|" short--self-close-tag-re
                       "\\|" short--close-tag-re "\\)")
               nil t)
              (progn
                (goto-char (match-beginning 0))
                (short-forward-sexp 1))
            (user-error "No more tags"))))))))

(defun short-backward-sexp (&optional arg)
  "Move backward across one Shorthand sexp (tag pair or self-closing tag).
With ARG, do it that many times.  Negative ARG means move forward."
  (interactive "^p")
  (or arg (setq arg 1))
  (if (< arg 0)
      (short-forward-sexp (- arg))
    (let ((case-fold-search nil))
      (dotimes (_ arg)
        (let ((orig (point)))
          (cond
           ;; Just after a close tag: find matching open tag
           ((save-excursion
              (and (re-search-backward short--close-tag-re nil t)
                   (= (match-end 0) orig)))
            (goto-char (match-beginning 0))
            (let ((tag-name (match-string 1))
                  (depth 1))
              (while (and (> depth 0)
                          (re-search-backward short--any-tag-re nil t))
                (cond
                 ((and (match-string 3)
                       (string= (match-string 3) tag-name))
                  (setq depth (1+ depth)))
                 ((and (match-string 1)
                       (string= (match-string 1) tag-name))
                  (setq depth (1- depth)))))
              (when (> depth 0)
                (goto-char orig)
                (user-error "No matching open tag for /%s/" tag-name))))
           ;; Just after a self-closing tag: move to its start
           ((save-excursion
              (and (re-search-backward short--self-close-tag-re nil t)
                   (= (match-end 0) orig)))
            (goto-char (match-beginning 0)))
           ;; Just after an open tag: move to its start
           ((save-excursion
              (and (re-search-backward short--open-tag-re nil t)
                   (= (match-end 0) orig)))
            (goto-char (match-beginning 0)))
           ;; Otherwise: search backward for previous tag end and process
           (t
            (if (re-search-backward
                 (concat "\\(?:" short--close-tag-re
                         "\\|" short--self-close-tag-re
                         "\\|" short--open-tag-re "\\)")
                 nil t)
                (progn
                  (goto-char (match-end 0))
                  (short-backward-sexp 1))
              (user-error "No more tags")))))))))

(defun short-backward-up-tag (&optional arg)
  "Move backward out of one level of tag nesting.
With ARG, do this that many times."
  (interactive "^p")
  (or arg (setq arg 1))
  (let ((case-fold-search nil))
    (dotimes (_ arg)
      (let ((depth 1)
            (start (point)))
        (while (and (> depth 0)
                    (re-search-backward short--any-tag-re nil t))
          (cond
           ((match-string 3) (setq depth (1+ depth)))  ; close tag
           ((match-string 1) (setq depth (1- depth)))  ; open tag
           (t nil)))                                    ; self-close: ignore
        (when (> depth 0)
          (goto-char start)
          (user-error "Already at top level"))))))

(defun short-down-tag (&optional arg)
  "Move forward into one level of tag nesting.
With ARG, do this that many times."
  (interactive "^p")
  (or arg (setq arg 1))
  (let ((case-fold-search nil))
    (dotimes (_ arg)
      (let ((start (point)))
        (if (re-search-forward short--open-tag-re nil t)
            (goto-char (match-end 0))
          (goto-char start)
          (user-error "No nested tag found"))))))

(defun short-up-tag (&optional arg)
  "Move forward out of one level of tag nesting.
With ARG, do this that many times."
  (interactive "^p")
  (or arg (setq arg 1))
  (let ((case-fold-search nil))
    (dotimes (_ arg)
      (let ((depth 1)
            (start (point)))
        (while (and (> depth 0)
                    (re-search-forward short--any-tag-re nil t))
          (cond
           ((match-string 1) (setq depth (1+ depth)))  ; open tag
           ((match-string 3) (setq depth (1- depth)))  ; close tag
           (t nil)))                                    ; self-close: ignore
        (when (> depth 0)
          (goto-char start)
          (user-error "Already at top level"))))))

;;; Alignment

(defun short-align (beg end)
  "Align `:' separators in Shorthand item lines within region BEG to END.
When called interactively with no active region, aligns the
consecutive block of item lines surrounding point."
  (interactive
   (if (use-region-p)
       (list (region-beginning) (region-end))
     (short--item-block-bounds)))
  (let ((indent-tabs-mode nil))
    (align-regexp beg end "\\(\\s-*\\) :" 1 1 nil)))

(defun short--item-block-bounds ()
  "Return (BEG END) for the contiguous block of item lines around point."
  (let ((item-re "^[ \t]*\\\\item\\\\")
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

(defvar short-mode-syntax-table
  (let ((st (make-syntax-table)))
    (modify-syntax-entry ?\\ "." st)
    (modify-syntax-entry ?/ "." st)
    (modify-syntax-entry ?: "." st)
    (modify-syntax-entry ?. "." st)
    (modify-syntax-entry ?_ "w" st)
    (modify-syntax-entry ?\" "\"" st)
    st)
  "Syntax table for `short-mode'.")

;;; Keymap

(defvar short-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-c C-f") #'short-find-matching-tag)
    (define-key map (kbd "C-c C-a") #'short-align)
    (define-key map [remap backward-up-list] #'short-backward-up-tag)
    (define-key map [remap down-list] #'short-down-tag)
    (define-key map [remap up-list] #'short-up-tag)
    map)
  "Keymap for `short-mode'.")

;;; Mode definition

;;;###autoload
(define-derived-mode short-mode prog-mode "Shorthand"
  "Major mode for editing Shorthand files.

Shorthand is the human-friendly syntax of LLMON.  It uses
\\\\tag\\\\ ... /tag/ markup.  Built-in tags include object, list, item
(structural), int, float, bool, none (type), and instr, exec, data, input,
output (semantic).  User-defined tags follow the same syntax with arbitrary
identifiers.

\\{short-mode-map}"
  :group 'short
  :syntax-table short-mode-syntax-table
  (setq-local font-lock-defaults '(short-font-lock-keywords))
  (setq-local indent-line-function #'short-indent-line)
  (setq-local forward-sexp-function #'short-forward-sexp)
  (setq-local comment-start nil)
  (setq-local comment-end nil))

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.short\\'" . short-mode))

(provide 'short-mode)
;;; short-mode.el ends here
