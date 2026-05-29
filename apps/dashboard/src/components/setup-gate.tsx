"use client";

/**
 * SetupGate — first-run redirect for the Phase 3 wizard.
 *
 * Mounted once inside the dashboard layout. On every client-side
 * navigation it polls `/api/v1/admin/setup/status`; if the user
 * hasn't configured at least one LLM key + one voice key, redirects
 * them to `/dashboard/setup`. Existing-user flows are unaffected
 * because `status.complete === true` short-circuits the redirect.
 *
 * Why client-side instead of middleware:
 *   Middleware runs server-side and would need access to the user's
 *   credentials to call the backend — we don't have session-cookie
 *   infrastructure in local-first mode. Client-side check is simpler,
 *   one round-trip, and matches the dashboard's other admin calls
 *   (which also flow through `api.ts` from the browser).
 *
 * Operator-escape hatch:
 *   If the wizard is stuck for any reason, the operator can manually
 *   set BYTEPLUS_LLM_API_KEY + BYTEPLUS_VOICE_API_KEY in `.env` and
 *   restart core — status flips to complete, the gate stops
 *   redirecting.
 */

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";

import { api } from "@/lib/api";

/**
 * Pages the gate must NEVER redirect away from when setup is
 * incomplete — because these are the pages where a user ADDS the
 * missing keys. Redirecting off them traps the user:
 *
 *   - `/dashboard/setup`        the first-run wizard itself
 *   - `/dashboard/settings`     the per-provider key editor (this is
 *                               how you add a second key — e.g. the
 *                               BytePlus VOICE key on top of an
 *                               already-saved LLM key)
 *   - `/dashboard/integrations` Google OAuth connect lives here
 *
 * The bug this fixes: `complete` requires BOTH an LLM key AND a voice
 * key. A user who'd saved only the LLM key had `complete=false`, so
 * the gate bounced them off /dashboard/settings back to /setup every
 * time they tried to add the voice key — an unwinnable loop that
 * also flickered the screen (paint Settings → status resolves →
 * router.replace to /setup → repeat). Exempting the self-serve
 * pages breaks the trap.
 */
const SETUP_SELF_SERVE_PREFIXES = [
  "/dashboard/setup",
  "/dashboard/settings",
  "/dashboard/integrations",
];

export function SetupGate() {
  const router = useRouter();
  const pathname = usePathname();
  // SWR caches the status across the dashboard so the gate doesn't
  // hammer the endpoint on every navigation. Refresh every 30s in
  // case the operator updates `.env` and restarts core in another
  // window — the gate then auto-stops redirecting without a manual
  // dashboard reload.
  const { data } = useSWR("setup-status", () => api.setupStatus(), {
    refreshInterval: 30_000,
    revalidateOnFocus: false,
  });

  useEffect(() => {
    // Wait for the first response — don't trigger a redirect on
    // initial render before we know the state.
    if (!data) return;
    if (data.complete) return;
    // Never redirect away from the pages where the user adds keys —
    // doing so traps them (and flickers the screen). Covers the page
    // itself + any sub-route (e.g. /dashboard/settings/...).
    const p = pathname || "";
    if (SETUP_SELF_SERVE_PREFIXES.some((base) => p === base || p.startsWith(base + "/"))) {
      return;
    }
    router.replace("/dashboard/setup");
  }, [data, pathname, router]);

  // Renders nothing — purely a behavioral component.
  return null;
}
