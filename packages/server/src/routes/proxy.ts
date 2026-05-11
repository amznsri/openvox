/**
 * Transparent proxy from `/api/v1/*` to the Python core service.
 *
 * The dashboard hits this gateway so we can layer auth, rate-limit, and
 * (optionally) caching at one Node-side seam without each browser
 * client knowing about the Python service.
 */
import type { FastifyPluginAsync } from "fastify";
import { request as undiciRequest } from "undici";

import { env } from "../config.js";

export const proxyRoutes: FastifyPluginAsync = async (fastify) => {
  // Match every method on every path under /api/v1/*
  fastify.all<{ Params: { "*": string } }>("/*", async (req, reply) => {
    // Strip the route prefix `/api/v1/` so the upstream path is clean.
    const target = `${env.CORE_API_URL}${req.url}`;
    const headers = { ...req.headers } as Record<string, string>;
    delete headers["host"];
    delete headers["content-length"];

    const body =
      req.method === "GET" || req.method === "HEAD"
        ? undefined
        : Buffer.isBuffer(req.body)
          ? req.body
          : typeof req.body === "string"
            ? req.body
            : JSON.stringify(req.body ?? {});

    const upstream = await undiciRequest(target, {
      method: req.method as any,
      headers,
      body,
    });

    reply.code(upstream.statusCode);
    for (const [k, v] of Object.entries(upstream.headers)) {
      if (k === "transfer-encoding" || k === "connection") continue;
      if (v !== undefined) reply.header(k, v as string);
    }
    return reply.send(await upstream.body.arrayBuffer().then((b) => Buffer.from(b)));
  });
};
