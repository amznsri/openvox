import type { FastifyPluginAsync } from "fastify";

export const healthRoutes: FastifyPluginAsync = async (fastify) => {
  fastify.get("/health", async () => ({ status: "ok", version: "0.1.0" }));
  fastify.get("/", async () => ({ service: "openvox-server", docs: "/api/v1" }));
};
