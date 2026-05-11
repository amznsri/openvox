import "dotenv/config";
import Fastify from "fastify";
import cors from "@fastify/cors";
import helmet from "@fastify/helmet";
import rateLimit from "@fastify/rate-limit";
import jwt from "@fastify/jwt";
import websocket from "@fastify/websocket";

import { env } from "./config.js";
import { healthRoutes } from "./routes/health.js";
import { proxyRoutes } from "./routes/proxy.js";
import { authRoutes } from "./routes/auth.js";
import { telephonyRoutes } from "./routes/telephony.js";
import { voiceWsRoute } from "./ws/voice.js";

const server = Fastify({
  logger: {
    level: env.LOG_LEVEL,
    transport:
      env.NODE_ENV === "development"
        ? { target: "pino-pretty", options: { colorize: true } }
        : undefined,
  },
  // Audio recordings and PDFs can be large.
  bodyLimit: 64 * 1024 * 1024,
});

await server.register(helmet, { contentSecurityPolicy: false });
await server.register(cors, { origin: true, credentials: true });
await server.register(rateLimit, { max: 1000, timeWindow: "1 minute" });
await server.register(jwt, { secret: env.JWT_SECRET });
await server.register(websocket);

// Fastify only parses JSON by default; everything else 415s before the
// proxy handler runs. Register passthrough parsers for the body shapes we
// need to forward to the Python core verbatim (file uploads + binary).
server.addContentTypeParser(
  /^multipart\//,
  { parseAs: "buffer", bodyLimit: 64 * 1024 * 1024 },
  (_req, body, done) => done(null, body),
);
server.addContentTypeParser(
  "application/octet-stream",
  { parseAs: "buffer", bodyLimit: 64 * 1024 * 1024 },
  (_req, body, done) => done(null, body),
);
// Plain text (e.g. small SSE / event payloads).
server.addContentTypeParser(
  /^text\//,
  { parseAs: "string" },
  (_req, body, done) => done(null, body),
);

server.decorate("authenticate", async function (request: any, reply: any) {
  if (env.OPENVOX_AUTH !== "enabled") return; // local-first: no-op
  try {
    await request.jwtVerify();
  } catch (err) {
    reply.code(401).send({ error: "unauthorized" });
  }
});

await server.register(healthRoutes);
await server.register(authRoutes, { prefix: "/api/v1/auth" });
await server.register(proxyRoutes, { prefix: "/api/v1" });
await server.register(telephonyRoutes, { prefix: "/api/v1/telephony" });
await server.register(voiceWsRoute);

try {
  await server.listen({ port: env.PORT, host: "0.0.0.0" });
  server.log.info(`OpenVox gateway on :${env.PORT} → core ${env.CORE_API_URL}`);
} catch (err) {
  server.log.error(err);
  process.exit(1);
}

declare module "fastify" {
  interface FastifyInstance {
    authenticate: (request: any, reply: any) => Promise<void>;
  }
}
