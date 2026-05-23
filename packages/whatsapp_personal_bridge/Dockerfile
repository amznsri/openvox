# OpenVox WhatsApp Personal bridge — Node service wrapping whatsapp-web.js.
#
# Image is opt-in: brought up via `docker compose --profile whatsapp up`.
# Most OpenVox users won't need this (personal WhatsApp via QR-scan is a
# niche channel; production users go through WhatsApp Business API which
# uses webhooks in the existing /telephony routes).
#
# Multi-stage to keep the runtime layer slim. Puppeteer's bundled
# Chromium is the heaviest dep at ~300 MB.
FROM node:20-bookworm-slim AS deps

# Chromium needs these to run in a sandboxed container.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxshmfence1 \
    libxss1 \
    libxtst6 \
    wget \
    xdg-utils \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev

# ── Runtime stage ───────────────────────────────────────────────────
FROM node:20-bookworm-slim AS runtime

# Pull the same OS libs Puppeteer needs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxshmfence1 \
    libxss1 \
    libxtst6 \
    wget \
    xdg-utils \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY package.json index.js ./

# Session data + Chromium user-data live under /data so they persist
# across container restarts (mount a docker volume here).
RUN mkdir -p /data/sessions
ENV SESSIONS_DIR=/data/sessions
ENV PORT=4100

EXPOSE 4100

CMD ["node", "index.js"]
