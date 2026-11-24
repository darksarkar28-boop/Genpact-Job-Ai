FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       coreutils \
       lmodern \
       pandoc \
       texlive-fonts-recommended \
       texlive-latex-extra \
       texlive-luatex \
       texlive-xetex \
    && rm -rf /var/lib/apt/lists/*

COPY compile.sh /worker/compile.sh
RUN chmod 0555 /worker/compile.sh
WORKDIR /work
ENTRYPOINT ["/worker/compile.sh"]
