/**
 * `<VoiceAgent />` — drop-in React component for embedding an OpenVox
 * voice agent on any page with three props:
 *
 *   <VoiceAgent server="http://localhost:3001" agentId="<uuid>" />
 *
 * Renders a single circular mic button and an optional transcript pane.
 * Apps that want full control can use `useVoiceSession()` directly
 * (this component is just a thin convenience wrapper).
 *
 * No styling library — inline styles only. Apps can pass `className`
 * or wrap in their own container to theme it.
 */

import { CSSProperties } from "react";
import { useVoiceSession, type SessionStatus } from "./useVoiceSession";

export interface VoiceAgentProps {
  /** Base URL of the OpenVox gateway, e.g. "http://localhost:3001". */
  server: string;
  /** Agent UUID — visit /dashboard/agents in the dashboard to get one. */
  agentId: string;
  /** Hide the rolling transcript below the button. Default: false. */
  hideTranscript?: boolean;
  /** Override the button label. Default: shows status. */
  label?: string;
  /** Inline style passthrough on the root container. */
  style?: CSSProperties;
  /** className passthrough on the root container. */
  className?: string;
}

const COLORS: Record<SessionStatus, string> = {
  idle:       "#6366f1",
  connecting: "#f59e0b",
  live:       "#10b981",
  ended:      "#64748b",
  error:      "#ef4444",
};

export function VoiceAgent({
  server,
  agentId,
  hideTranscript,
  label,
  style,
  className,
}: VoiceAgentProps) {
  const { status, transcript, error, start, stop, interrupt } = useVoiceSession({ server, agentId });

  const onClick = () => {
    if (status === "live") {
      interrupt();
      void stop();
    } else {
      void start();
    }
  };

  return (
    <div className={className} style={{ display: "flex", flexDirection: "column", gap: 16, ...style }}>
      <button
        onClick={onClick}
        aria-label={status === "live" ? "Stop call" : "Start call"}
        style={{
          width: 72,
          height: 72,
          borderRadius: 36,
          background: COLORS[status],
          color: "#fff",
          border: "none",
          cursor: "pointer",
          boxShadow: status === "live" ? "0 0 0 6px rgba(16,185,129,0.2)" : "0 4px 12px rgba(0,0,0,0.15)",
          fontSize: 24,
          transition: "all 0.15s ease",
          alignSelf: "flex-start",
        }}
        disabled={status === "connecting"}
      >
        {status === "live" ? "■" : status === "connecting" ? "…" : "●"}
      </button>
      <div style={{ fontSize: 13, color: "#64748b" }}>
        {label ?? (
          status === "idle" ? "Click to start" :
          status === "connecting" ? "Connecting…" :
          status === "live" ? "Listening… (click to stop)" :
          status === "ended" ? "Session ended" :
          status === "error" ? `Error: ${error}` :
          ""
        )}
      </div>
      {!hideTranscript && transcript.length > 0 && (
        <div
          style={{
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            padding: 12,
            maxHeight: 240,
            overflowY: "auto",
            fontSize: 14,
            background: "#f8fafc",
          }}
        >
          {transcript.map((line, i) => (
            <div key={i} style={{
              marginBottom: 6,
              opacity: line.pending ? 0.6 : 1,
              color: line.role === "user" ? "#0f172a" : line.role === "skill" ? "#7c3aed" : "#0369a1",
            }}>
              <strong style={{ marginRight: 8, textTransform: "uppercase", fontSize: 10, letterSpacing: 1 }}>
                {line.role}
              </strong>
              {line.text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
