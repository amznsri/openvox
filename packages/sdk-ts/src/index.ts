/**
 * @openvox/sdk — TypeScript client for the OpenVox API.
 *
 * Works in Node and the browser. Provides:
 *   - REST helpers for agents, templates, sessions, skills
 *   - VoiceSession class that streams a microphone (browser) or
 *     pre-recorded PCM frames (Node) over the live WS pipeline.
 */

export * from "./types.js";
export { OpenVoxClient } from "./client.js";
export { VoiceSession } from "./voice-session.js";
