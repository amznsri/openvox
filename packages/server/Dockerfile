# Server build — npm so we don't fight pnpm's symlink/virtual-store quirks
# inside Alpine. The server has no workspace deps.
FROM node:20-alpine AS builder
WORKDIR /app
ENV NODE_ENV=development
COPY package.json ./
RUN npm install --include=dev
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json ./
EXPOSE 3001
CMD ["node", "dist/index.js"]
