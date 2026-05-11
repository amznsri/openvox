/**
 * Auth routes — only meaningful when OPENVOX_AUTH=enabled.
 *
 * In local-first mode we expose a `/me` that returns a synthetic
 * "local user" so the dashboard doesn't have to special-case the
 * unauthenticated path.
 */
import type { FastifyPluginAsync } from "fastify";

import { env } from "../config.js";

export const authRoutes: FastifyPluginAsync = async (fastify) => {
  fastify.get("/me", async (req) => {
    if (env.OPENVOX_AUTH !== "enabled") {
      return { id: "local", name: "Local User", provider: "local" };
    }
    try {
      const payload = (await req.jwtVerify()) as { sub: string; email?: string };
      return { id: payload.sub, email: payload.email };
    } catch {
      return { id: null };
    }
  });

  // OAuth scaffolds — endpoints exist so the dashboard doesn't 404 even
  // when OAuth credentials aren't set; they redirect to a polite "set
  // your env vars" page in that case.
  fastify.get("/github/start", async (_req, reply) => {
    if (!env.GITHUB_OAUTH_CLIENT_ID) return reply.code(501).send({ error: "github oauth not configured" });
    const u = new URL("https://github.com/login/oauth/authorize");
    u.searchParams.set("client_id", env.GITHUB_OAUTH_CLIENT_ID);
    u.searchParams.set("scope", "read:user user:email");
    return reply.redirect(u.toString());
  });

  fastify.get("/google/start", async (_req, reply) => {
    if (!env.GOOGLE_OAUTH_CLIENT_ID) return reply.code(501).send({ error: "google oauth not configured" });
    const u = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    u.searchParams.set("client_id", env.GOOGLE_OAUTH_CLIENT_ID);
    u.searchParams.set("response_type", "code");
    u.searchParams.set("scope", "openid email profile");
    return reply.redirect(u.toString());
  });
};
