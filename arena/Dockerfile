FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates procps tzdata \
    && rm -rf /var/lib/apt/lists/*

# Lightpanda : navigateur sans tete ultra rapide (binaire statique), appele en sous-processus
# (`lightpanda fetch --dump html <url>`) ou en serveur CDP.
RUN curl -fsSL -o /usr/local/bin/lightpanda \
      https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux \
    && chmod +x /usr/local/bin/lightpanda

# Scout (kiryano/Scout, licence MIT) : scraper de profils GitHub utilise par la course aux leads, commit fige
ARG SCOUT_COMMIT=183868d4fe4b9800d649d814ad8685750cc5637d
RUN mkdir -p /srv/vendor/Scout     && curl -fsSL https://codeload.github.com/kiryano/Scout/tar.gz/${SCOUT_COMMIT} | tar xz --strip-components=1 -C /srv/vendor/Scout

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app /srv/app
EXPOSE 8800
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800"]
