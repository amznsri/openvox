# Dashboard build — npm with the full repo as context (so workspace-style
# imports work even though we don't actually use pnpm workspace here).
FROM node:20-alpine AS builder
WORKDIR /app

COPY apps/dashboard/package.json ./
RUN npm install --include=dev --ignore-scripts

COPY apps/dashboard/. ./

ENV NODE_ENV=production
ENV BUILD_OUTPUT=standalone

# Docker mode serves the dashboard at :3000 but the API at :8000 — two
# DIFFERENT origins — so the same-origin fallback in api.ts won't reach
# the API. We must bake an explicit API/WS URL into the client bundle at
# build time (NEXT_PUBLIC_* are inlined during `next build`). These come
# from build args supplied by docker-compose.yml; the defaults keep a
# bare `docker build` working too.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL

RUN npm run build

# ─── Runner ────────────────────────────────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
