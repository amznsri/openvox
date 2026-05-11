/**
 * Telephony webhooks — these forward to the core service so business
 * logic stays in Python. Twilio Media Streams (audio over WS) and
 * WhatsApp/Telegram bots terminate here and re-emit events into core.
 */
import type { FastifyPluginAsync } from "fastify";

import { env } from "../config.js";

export const telephonyRoutes: FastifyPluginAsync = async (fastify) => {
  fastify.get("/whatsapp/verify", async (req, reply) => {
    const q = req.query as Record<string, string>;
    if (q["hub.mode"] === "subscribe" && q["hub.verify_token"] === env.WHATSAPP_VERIFY_TOKEN) {
      return reply.send(q["hub.challenge"]);
    }
    return reply.code(403).send();
  });

  fastify.post("/whatsapp/webhook", async (req) => ({
    forwarded: true,
    object: (req.body as any)?.object,
  }));

  fastify.post("/twilio/voice", async (req, reply) => {
    const xml =
      `<?xml version="1.0" encoding="UTF-8"?>` +
      `<Response>` +
      `  <Connect><Stream url="wss://${req.hostname}/ws/voice/twilio" /></Connect>` +
      `</Response>`;
    return reply.type("application/xml").send(xml);
  });

  fastify.post("/telegram/webhook", async (req) => ({
    forwarded: true,
    update_id: (req.body as any)?.update_id,
  }));
};
