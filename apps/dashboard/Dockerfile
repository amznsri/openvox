# Dashboard build — npm with the full repo as context (so workspace-style
# imports work even though we don't actually use pnpm workspace here).
FROM node:20-alpine AS builder
WORKDIR /app

COPY apps/dashboard/package.json ./
RUN npm install --include=dev --ignore-scripts

COPY apps/dashboard/. ./

ENV NODE_ENV=production
ENV BUILD_OUTPUT=standalone
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
