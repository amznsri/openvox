"use client";

import { Mic, MicOff, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

export type MicState = "idle" | "connecting" | "listening" | "speaking" | "error";

const labels: Record<MicState, string> = {
  idle: "Tap to talk",
  connecting: "Connecting…",
  listening: "Listening…",
  speaking: "Agent speaking…",
  error: "Tap to retry",
};

export function MicButton({
  state,
  onClick,
}: {
  state: MicState;
  onClick: () => void;
}) {
  const isActive = state === "listening" || state === "speaking";
  const Icon = state === "error" ? MicOff : state === "connecting" ? Loader2 : Mic;
  return (
    <div className="flex flex-col items-center gap-3">
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "relative h-28 w-28 rounded-full transition-all flex items-center justify-center",
          "bg-gradient-to-br from-violet-500 to-cyan-400",
          "shadow-[0_0_60px_-10px_rgba(139,92,246,0.7)]",
          "hover:scale-[1.03] active:scale-95",
          isActive && "animate-pulse-glow",
          state === "error" && "from-danger to-amber-500",
        )}
        aria-label={labels[state]}
      >
        <span className="absolute inset-1.5 rounded-full bg-background/70 backdrop-blur-md flex items-center justify-center">
          <Icon
            className={cn(
              "h-10 w-10 text-foreground",
              state === "connecting" && "animate-spin",
            )}
          />
        </span>
      </button>
      <div className="text-sm text-muted-foreground tabular-nums">{labels[state]}</div>
    </div>
  );
}
