/**
 * OpenVox WhatsApp Personal bridge — Node-side companion to the Python
 * core's `telephony/whatsapp_personal.py`.
 *
 * Why a separate process:
 *   whatsapp-web.js is Node-only and pulls in Puppeteer + a Chromium-
 *   compatible runtime. Keeping it out of the Python core keeps the
 *   core image small and avoids language/dep mixing. The bridge runs
 *   as an opt-in Docker service behind `--profile whatsapp`.
 *
 * Multi-agent multiplexing:
 *   Each WhatsApp-Personal-enabled agent gets its own `Client`
 *   instance keyed by `agent_id`. Sessions persist to disk
 *   (LocalAuth in `/data/sessions/<agent_id>/`) so reconnect after a
 *   bridge restart doesn't require a fresh QR scan.
 *
 * Inbound message flow:
 *   Each Client's `message` event POSTs to PYTHON_WEBHOOK with
 *   `{ agent_id, from, body, type, timestamp }`. The Python core's
 *   `/api/v1/telephony/whatsapp_personal/inbound` handler runs the
 *   LLM and POSTs back to `/sessions/<agent_id>/send` to reply.
 *
 * HTTP API:
 *   POST   /sessions/:agent_id/start            spin up Client + LocalAuth, returns immediately
 *   GET    /sessions/:agent_id/status           { status, qr?, info? }
 *   POST   /sessions/:agent_id/send             { to, body }     send text
 *   DELETE /sessions/:agent_id                  destroy + cleanup session files (NB: clears auth)
 *   GET    /health                              liveness probe
 *
 * IMPORTANT — Meta TOS:
 *   whatsapp-web.js drives an unofficial protocol. Accounts using it
 *   risk permanent bans, with no appeal. We surface this prominently
 *   in the dashboard UI; the bridge itself just runs the library.
 */
"use strict";

const path = require("path");
const fs = require("fs/promises");
const express = require("express");
const QRCode = require("qrcode");
const fetch = require("node-fetch");
const { Client, LocalAuth } = require("whatsapp-web.js");

// ── Config from environment ─────────────────────────────────────────
const PORT = parseInt(process.env.PORT || "4100", 10);
const PYTHON_WEBHOOK =
  process.env.PYTHON_WEBHOOK || "http://core:8000/api/v1/telephony/whatsapp_personal/inbound";
const SESSIONS_DIR = process.env.SESSIONS_DIR || "/data/sessions";

// ── Per-agent client registry ───────────────────────────────────────
// Maps agent_id → { client, status, qr, info, lastError }
const sessions = new Map();

/**
 * Initialise (or re-initialise) the WhatsApp Client for an agent.
 * Idempotent: calling on an existing session destroys + recreates.
 */
async function startClient(agentId) {
  // Tear down existing if any.
  const existing = sessions.get(agentId);
  if (existing && existing.client) {
    try {
      await existing.client.destroy();
    } catch (e) {
      console.warn(`[${agentId}] error destroying old client:`, e.message);
    }
  }

  const state = {
    client: null,
    status: "initializing", // initializing | qr | authenticated | ready | disconnected | error
    qr: null, // PNG data-URL when status==="qr"
    info: null, // { wid, pushname, platform } once ready
    lastError: null,
  };
  sessions.set(agentId, state);

  // LocalAuth persists session keys so subsequent restarts skip QR.
  const client = new Client({
    authStrategy: new LocalAuth({
      clientId: agentId,
      dataPath: SESSIONS_DIR,
    }),
    puppeteer: {
      // Required for headless Chromium inside our Docker container.
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    },
  });
  state.client = client;

  client.on("qr", async (qrStr) => {
    try {
      state.qr = await QRCode.toDataURL(qrStr, { margin: 1, width: 256 });
      state.status = "qr";
      console.log(`[${agentId}] qr ready (${qrStr.length} chars)`);
    } catch (e) {
      console.error(`[${agentId}] qr encode failed:`, e);
    }
  });

  client.on("authenticated", () => {
    state.status = "authenticated";
    state.qr = null;
    console.log(`[${agentId}] authenticated`);
  });

  client.on("auth_failure", (msg) => {
    state.status = "error";
    state.lastError = `auth_failure: ${msg}`;
    console.error(`[${agentId}] auth_failure:`, msg);
  });

  client.on("ready", () => {
    state.status = "ready";
    state.qr = null;
    state.info = {
      wid: client.info?.wid?._serialized || null,
      pushname: client.info?.pushname || null,
      platform: client.info?.platform || null,
    };
    console.log(`[${agentId}] ready — connected as ${state.info.pushname}`);
  });

  client.on("disconnected", (reason) => {
    state.status = "disconnected";
    state.lastError = `disconnected: ${reason}`;
    console.log(`[${agentId}] disconnected:`, reason);
  });

  // Inbound message → forward to Python core.
  client.on("message", async (msg) => {
    // Ignore status messages, groups (for now), our own outbound echoes.
    if (msg.fromMe || msg.isStatus) return;
    if (msg.from && msg.from.endsWith("@g.us")) return; // skip groups
    const payload = {
      agent_id: agentId,
      from: msg.from,
      body: msg.body || "",
      type: msg.type,
      timestamp: msg.timestamp,
      has_media: msg.hasMedia,
    };
    try {
      const r = await fetch(PYTHON_WEBHOOK, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        console.warn(`[${agentId}] webhook returned ${r.status}`);
      }
    } catch (e) {
      console.error(`[${agentId}] webhook post failed:`, e.message);
    }
  });

  // Fire-and-forget; QR / ready events update state asynchronously.
  client.initialize().catch((e) => {
    state.status = "error";
    state.lastError = `initialize: ${e.message}`;
    console.error(`[${agentId}] initialize failed:`, e);
  });

  return state;
}

// ── Express HTTP API ────────────────────────────────────────────────
const app = express();
app.use(express.json({ limit: "5mb" }));

app.get("/health", (_req, res) => {
  res.json({
    ok: true,
    sessions: Array.from(sessions.keys()),
  });
});

app.post("/sessions/:agentId/start", async (req, res) => {
  const { agentId } = req.params;
  if (!agentId) return res.status(400).json({ error: "agent_id required" });
  try {
    await startClient(agentId);
    res.json({ started: true, agent_id: agentId });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get("/sessions/:agentId/status", (req, res) => {
  const { agentId } = req.params;
  const state = sessions.get(agentId);
  if (!state) {
    return res.json({ status: "not_started" });
  }
  res.json({
    status: state.status,
    qr: state.qr || null,
    info: state.info || null,
    last_error: state.lastError || null,
  });
});

app.post("/sessions/:agentId/send", async (req, res) => {
  const { agentId } = req.params;
  const { to, body } = req.body || {};
  if (!to || !body) {
    return res.status(400).json({ error: "to + body required" });
  }
  const state = sessions.get(agentId);
  if (!state || state.status !== "ready") {
    return res.status(503).json({
      error: "session not ready",
      status: state ? state.status : "not_started",
    });
  }
  try {
    await state.client.sendMessage(to, body);
    res.json({ sent: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.delete("/sessions/:agentId", async (req, res) => {
  const { agentId } = req.params;
  const state = sessions.get(agentId);
  if (state && state.client) {
    try {
      await state.client.destroy();
    } catch (e) {
      console.warn(`[${agentId}] destroy error:`, e.message);
    }
  }
  sessions.delete(agentId);
  // Also wipe the persisted session so a fresh connect requires re-scan.
  try {
    const dir = path.join(SESSIONS_DIR, `session-${agentId}`);
    await fs.rm(dir, { recursive: true, force: true });
  } catch (e) {
    console.warn(`[${agentId}] session dir cleanup failed:`, e.message);
  }
  res.json({ disconnected: true });
});

// ── Graceful shutdown ───────────────────────────────────────────────
async function shutdown() {
  console.log("shutting down — destroying all clients");
  for (const [agentId, state] of sessions) {
    try {
      await state.client?.destroy();
    } catch (e) {
      console.warn(`[${agentId}] destroy error:`, e.message);
    }
  }
  process.exit(0);
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

app.listen(PORT, "0.0.0.0", () => {
  console.log(`openvox-whatsapp-personal-bridge listening on :${PORT}`);
  console.log(`  PYTHON_WEBHOOK = ${PYTHON_WEBHOOK}`);
  console.log(`  SESSIONS_DIR   = ${SESSIONS_DIR}`);
});
