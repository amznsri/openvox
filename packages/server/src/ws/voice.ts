/**
 * Voice WebSocket bridge — proxies the browser's WS to the Python core.
 *
 * @fastify/websocket v11 (Fastify v5) changed the handler shape: it now
 * receives the WebSocket directly instead of a `{ socket }` wrapper.
 * We rely on that newer signature.
 */
import type { FastifyPluginAsync } from "fastify";
import type { WebSocket as WSType } from "ws";
import WebSocket from "ws";

import { env } from "../config.js";

export const voiceWsRoute: FastifyPluginAsync = async (fastify) => {
  fastify.get(
    "/ws/voice",
    { websocket: true },
    // @fastify/websocket v11 hands the socket directly.
    (socket: WSType /* , request */) => {
      const upstreamUrl = env.CORE_API_URL.replace(/^http/, "ws") + "/ws/voice";
      const upstream = new WebSocket(upstreamUrl);

      const buffered: (string | Buffer)[] = [];
      let upstreamReady = false;

      upstream.on("open", () => {
        upstreamReady = true;
        for (const msg of buffered) upstream.send(msg);
        buffered.length = 0;
      });

      upstream.on("message", (data: Buffer, isBinary: boolean) => {
        // `ws` always delivers a Buffer on the server side; only `isBinary`
        // tells us whether it was a binary or text frame. Forwarding
        // text-frames as binary would make the browser play JSON bytes as
        // PCM (audible as fast tapping/static).
        try {
          if (isBinary) {
            socket.send(data, { binary: true });
          } else {
            socket.send(data.toString("utf-8"));
          }
        } catch {
          /* socket already closed */
        }
      });

      upstream.on("close", () => {
        try {
          socket.close();
        } catch {
          /* ignore */
        }
      });

      upstream.on("error", (e) => {
        try {
          socket.send(JSON.stringify({ type: "error", message: String(e) }));
        } catch {
          /* ignore */
        }
      });

      socket.on("message", (data: Buffer, isBinary: boolean) => {
        const out: Buffer | string = isBinary ? data : data.toString();
        if (upstreamReady) {
          upstream.send(out);
        } else {
          buffered.push(out);
        }
      });

      socket.on("close", () => {
        try {
          upstream.close();
        } catch {
          /* ignore */
        }
      });
    },
  );
};
