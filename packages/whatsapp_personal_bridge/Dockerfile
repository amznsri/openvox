# OpenVox WhatsApp Personal bridge — Node service wrapping whatsapp-web.js.
#
# Image is opt-in: brought up via `docker compose --profile whatsapp up`.
# Most OpenVox users won't need this (personal WhatsApp via QR-scan is a
# niche channel; production users go through WhatsApp Business API which
# uses webhooks in the existing /telephony routes).
#
# Chromium strategy:
#   We install Chromium from Debian's apt repos rather than relying on
#   Puppeteer's automatic download. Reasons:
#     1. Puppeteer's downloader is brittle inside Docker — it caches to
#        /root/.cache/puppeteer/ which may or may not exist depending on
#        WORKDIR and uid mappings, and the cache binary name changes
#        between Puppeteer versions. We hit "Could not find Chrome
#        (ver. 146.0.7680.31)" with the default flow.
#     2. apt's chromium pulls its own library deps as transitive
#        dependencies — no need to enumerate every libgtk / libnss /
#        libxshmfence we'd otherwise need.
#     3. apt's chromium gets security updates from Debian.
#   Puppeteer is told NOT to download via PUPPETEER_SKIP_DOWNLOAD=true,
#   and to use the apt binary via PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium.

# ── Deps stage ──────────────────────────────────────────────────────
FROM node:20-bookworm-slim AS deps

WORKDIR /app

# Skip the Chromium download during `npm install` — we use the system
# binary at runtime. This shaves ~150 MB from the build cache + cuts
# install time noticeably.
ENV PUPPETEER_SKIP_DOWNLOAD=true

COPY package.json ./
RUN npm install --omit=dev

# ── Runtime stage ───────────────────────────────────────────────────
FROM node:20-bookworm-slim AS runtime

# Install Chromium + the minimum extras Puppeteer's headless launch
# script needs (fonts-liberation prevents font-fallback render artefacts;
# wget is occasionally invoked by Puppeteer's bootstrap).
# apt's chromium handles its own library transitive deps so we don't
# need to enumerate libgtk-3-0, libnss3, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    fonts-liberation \
    wget \
    xdg-utils \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY package.json index.js ./

# Tell Puppeteer + whatsapp-web.js which Chromium to use. The Node
# code reads PUPPETEER_EXECUTABLE_PATH and passes it to puppeteer.launch().
ENV PUPPETEER_SKIP_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# Session data + Chromium user-data live under /data so they persist
# across container restarts (mount a docker volume here).
RUN mkdir -p /data/sessions
ENV SESSIONS_DIR=/data/sessions
ENV PORT=4100

EXPOSE 4100

CMD ["node", "index.js"]
