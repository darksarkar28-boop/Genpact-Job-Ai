FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       coreutils \
       libreoffice-writer \
       lmodern \
       python3 \
       python3-docx \
       texlive-fonts-extra \
       texlive-fonts-recommended \
       texlive-latex-extra \
       texlive-luatex \
       texlive-xetex \
    && rm -rf /var/lib/apt/lists/*

COPY compile.sh /worker/compile.sh
COPY render_docx.py /worker/render_docx.py
RUN chmod 0555 /worker/compile.sh /worker/render_docx.py
WORKDIR /work
ENTRYPOINT ["/worker/compile.sh"]
