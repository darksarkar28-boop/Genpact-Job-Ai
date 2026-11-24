#!/bin/sh
set -eu

format="${1:-}"
engine="${2:-}"

case "$format" in
  pdf)
    case "$engine" in
      lualatex|xelatex) ;;
      *) echo "unsupported TeX engine" >&2; exit 64 ;;
    esac
    timeout 45s "$engine" -interaction=nonstopmode -halt-on-error -no-shell-escape document.tex >/tmp/compile.log 2>&1
    test -s document.pdf
    mv document.pdf output.pdf
    ;;
  docx)
    timeout 45s pandoc --from=markdown --to=docx document.md -o output.docx >/tmp/compile.log 2>&1
    test -s output.docx
    ;;
  *)
    echo "unsupported output format" >&2
    exit 64
    ;;
esac
