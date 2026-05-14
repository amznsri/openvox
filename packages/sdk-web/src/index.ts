/**
 * @openvox/web — React SDK for embedding OpenVox voice agents.
 *
 * Two entry points:
 *   - <VoiceAgent server="..." agentId="..." />     drop-in component
 *   - useVoiceSession({ server, agentId })          hook for custom UIs
 */

export { VoiceAgent, type VoiceAgentProps } from "./VoiceAgent";
export { useVoiceSession, type SessionStatus, type TranscriptLine, type UseVoiceSessionOptions } from "./useVoiceSession";
export { MicCapture, PcmPlayer, downsampleToS16 } from "./audio";
export { VoiceWS, type VoiceEvent, type StartArgs } from "./ws";
