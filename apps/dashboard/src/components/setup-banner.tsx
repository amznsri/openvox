"use client";

/**
 * SetupBanner — yellow banner shown on every dashboard page when
 * the first-run wizard hasn't completed.
 *
 * Today this RARELY shows because `SetupGate` redirects users with
 * incomplete setup straight to `/dashboard/setup`. It's a safety net
 * for the edge cases the gate misses:
 *
 *   - User configured a partial set of keys (e.g. only LLM, no voice),
 *     navigated back out of the wizard, then the gate redirects them
 *     in a loop. We need an escape hatch + a visible reminder.
 *
 *   - User configured everything, then deleted a key via the admin
 *     API (DELETE /api/v1/admin/setup/keys). Setup becomes incomplete
 *     mid-session; SetupGate's 30s SWR refresh eventually triggers a
 *     redirect, but the banner shows immediately on the next render.
 *
 *   - Operator runs OpenVox with `OPENVOX_AUTH=disabled` AND no env
 *     keys AND no wizard keys. Bypassing the gate by direct URL
 *     would otherwise leave them confused about why every feature
 *     errors with "not configured".
 *
 * Design choice: ONE component, always rendered in the dashboard
 * layout. If status is complete or still loading, it renders nothing.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";

import { api } from "@/lib/api";

export function SetupBanner() {
  const pathname = usePathname();
  // Same SWR key as SetupGate — they share the cached response. The
  // gate's 30s refresh interval covers the banner too.
  const { data } = useSWR("setup-status", () => api.setupStatus(), {
    refreshInterval: 30_000,
    revalidateOnFocus: false,
  });

  // Render nothing while we don't know the state yet — avoids a
  // flash of "needs setup" on first paint for fully-configured users.
  if (!data) return null;
  if (data.complete) return null;
  // Don't show ON the setup page itself — would be redundant + ugly.
  if (pathname === "/dashboard/setup") return null;

  // Be specific about WHAT's missing so the user knows what to fix.
  const missing: string[] = [];
  if (!data.have_llm) missing.push("an LLM key");
  if (!data.have_voice) missing.push("a voice key");
  const missingText = missing.join(" and ");

  return (
    <div className="bg-amber-500/15 border-b border-amber-500/40 px-4 py-2 text-sm flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-amber-200">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 20 20"
          fill="currentColor"
          className="h-4 w-4 shrink-0"
          aria-hidden="true"
        >
          <path
            fillRule="evenodd"
            d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z"
            clipRule="evenodd"
          />
        </svg>
        <span>
          Setup not finished —{" "}
          <strong>add {missingText}</strong>{" "}
          before voice features will work.
        </span>
      </div>
      <Link
        href="/dashboard/setup"
        className="rounded-md bg-amber-500/30 hover:bg-amber-500/50 text-amber-50 px-3 py-1 text-xs font-medium transition-colors shrink-0"
      >
        Finish setup →
      </Link>
    </div>
  );
}
