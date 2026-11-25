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
    timeout 30s python3 /worker/render_docx.py document.json output.docx >/tmp/docx.log 2>&1
    test -s output.docx
    rm -rf /tmp/lo-profile /tmp/lo-out
    mkdir -p /tmp/lo-out
    timeout 35s libreoffice --headless --nologo --nodefault --nofirststartwizard \
      -env:UserInstallation=file:///tmp/lo-profile \
      --convert-to pdf --outdir /tmp/lo-out output.docx >/tmp/lo.log 2>&1
    test -s /tmp/lo-out/output.pdf
    cp /tmp/lo-out/output.pdf output.pdf
    ;;
  *)
    echo "unsupported output format" >&2
    exit 64
    ;;
esac
