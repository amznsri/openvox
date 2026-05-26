"use client";

/**
 * Integrations — third-party OAuth connections (Phase 1.5).
 *
 * Today this surfaces Google (Gmail + Calendar). Adding a new provider
 * later (Microsoft 365, HubSpot, …) is "render another <ProviderCard>"
 * because the backend follows the same `/api/v1/integrations/<provider>/*`
 * convention.
 *
 * URL parameters this page handles:
 *   ?google=success&google_email=<email>   — OAuth callback redirected
 *                                              here after a successful
 *                                              connect; show a toast.
 *   ?google=error&google_msg=<text>         — Callback redirected here
 *                                              after a failure; show
 *                                              an error toast.
 * The toast cleans itself out of the URL after 6 seconds so a hard-
 * refresh of the integrations page doesn't show stale messages.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import useSWR from "swr";
import {
  CheckCircle2,
  Link as LinkIcon,
  Loader2,
  Mail,
  Unplug,
  XCircle,
  Calendar,
} from "lucide-react";

import { api, type GoogleIntegrationAccount } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";

type GoogleStatus = {
  configured: boolean;
  accounts: GoogleIntegrationAccount[];
};

// Next.js 14's static export (`output: 'export'`) requires every
// `useSearchParams()` caller to sit inside a `<Suspense>` boundary —
// otherwise the prerender pass bails out with the
// "missing-suspense-with-csr-bailout" error and the wheel build (which
// embeds the static export) fails. So we split into:
//
//   - default export `IntegrationsPage` — pure-server-safe, wraps
//     `IntegrationsPageInner` in `<Suspense>`.
//   - `IntegrationsPageInner` — the original component, free to use
//     client-side navigation hooks.
//
// The `<Suspense fallback>` is intentionally minimal: the only thing
// the inner component USES from the searchParams is the post-OAuth-
// callback toast, which is itself transient. A flash of "loading…"
// would be more disruptive than just briefly showing the page without
// the toast.
export default function IntegrationsPage() {
  return (
    <Suspense fallback={null}>
      <IntegrationsPageInner />
    </Suspense>
  );
}


function IntegrationsPageInner() {
  const { data, isLoading, mutate } = useSWR<GoogleStatus>(
    "google-integrations",
    () => api.googleIntegrationStatus(),
  );

  // Toast surfaced from the OAuth callback's query params.
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [toast, setToast] = useState<{
    kind: "success" | "error";
    text: string;
  } | null>(null);

  useEffect(() => {
    const flag = search.get("google");
    if (!flag) return;
    if (flag === "success") {
      const email = search.get("google_email") || "your Google account";
      setToast({ kind: "success", text: `Connected ${email}` });
    } else if (flag === "error") {
      const msg = search.get("google_msg") || "Connect failed";
      setToast({ kind: "error", text: msg });
    }
    // Strip the query string after rendering once so refresh doesn't
    // re-fire the toast.
    const t = setTimeout(() => {
      setToast(null);
      router.replace(pathname);
    }, 6000);
    return () => clearTimeout(t);
    // We deliberately depend ONLY on the search-key contents — not the
    // router/pathname references, which are stable per Next.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search.toString()]);


  return (
    <div className="container py-8 space-y-6 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <LinkIcon className="h-5 w-5 text-violet-300" />
          Integrations
        </h1>
        <p className="text-muted-foreground text-sm mt-1">
          Connect third-party accounts so your agents can read email, schedule
          meetings, and look up contacts on your behalf. Tokens are
          encrypted at rest in <code className="text-foreground">~/.openvox/</code>{" "}
          and never leave this machine.
        </p>
      </div>

      {toast && (
        <div
          className={
            toast.kind === "success"
              ? "rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm flex items-start gap-2"
              : "rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm flex items-start gap-2"
          }
        >
          {toast.kind === "success" ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-400 mt-0.5 shrink-0" />
          ) : (
            <XCircle className="h-4 w-4 text-red-400 mt-0.5 shrink-0" />
          )}
          <div>{toast.text}</div>
        </div>
      )}

      <GoogleCard
        status={data}
        loading={isLoading}
        onChanged={() => mutate()}
      />
    </div>
  );
}

function GoogleCard({
  status,
  loading,
  onChanged,
}: {
  status: GoogleStatus | undefined;
  loading: boolean;
  onChanged: () => void;
}) {
  const accounts = status?.accounts || [];
  const configured = status?.configured ?? false;

  // ?focus=<email> deeplink. Lands the user from the command-palette's
  // "Disconnect alice@example.com" action. The matching AccountRow
  // scrolls into view + pulses for ~3s. Deliberately does NOT
  // auto-click Disconnect — destructive writes need a real human
  // click, especially because voice transcripts of email addresses
  // are too lossy to trust ("alice at example dot com" → "[alice at
  // example dot com]").
  const search = useSearchParams();
  const focusEmail = (search.get("focus") || "").trim().toLowerCase();

  const connect = () => {
    // The /start endpoint 302's to Google. Browser navigation handles
    // the redirect; the callback will bring us back here with
    // ?google=success or ?google=error.
    window.location.href = api.googleIntegrationStartUrl();
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Mail className="h-4 w-4 text-rose-300" />
          Google (Gmail + Calendar)
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="text-muted-foreground">
          Connect your Google account to let agents read inbox, send emails,
          look up contacts from history, and read/write your calendar.
          Multiple accounts can be connected at once (e.g. personal + work) —
          agents disambiguate by email.
        </p>

        {!configured && (
          // Inline form for first-time OAuth client setup. Until v0.2.12
          // this was a static warning + "paste them on the Settings
          // page" hint — but the Settings page never had a Google
          // section, leaving env-var-edit as the only working path.
          // Now the form lives where it's needed: right above the
          // Connect Gmail button it's gating.
          <GoogleOAuthClientForm onSaved={onChanged} />
        )}

        {loading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : (
          <>
            {accounts.length > 0 ? (
              <div className="space-y-2">
                {accounts.map((account) => (
                  <AccountRow
                    key={account.user_email}
                    account={account}
                    onDisconnect={onChanged}
                    focused={
                      focusEmail.length > 0 &&
                      account.user_email.toLowerCase() === focusEmail
                    }
                  />
                ))}
              </div>
            ) : (
              <div className="text-center py-6 text-muted-foreground text-xs">
                No Google accounts connected yet.
              </div>
            )}

            <div className="flex items-center justify-between pt-2 border-t border-border/40">
              <div className="text-xs text-muted-foreground flex items-center gap-3">
                <span className="flex items-center gap-1">
                  <Mail className="h-3 w-3" /> Gmail
                </span>
                <span className="flex items-center gap-1">
                  <Calendar className="h-3 w-3" /> Calendar
                </span>
              </div>
              <Button
                onClick={connect}
                disabled={!configured}
                className="gap-2"
              >
                <LinkIcon className="h-4 w-4" />
                {accounts.length === 0 ? "Connect Gmail" : "Connect another account"}
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function GoogleOAuthClientForm({ onSaved }: { onSaved: () => void }) {
  // Inline form for the maintainer's Google OAuth client credentials.
  // Posts to /api/v1/admin/setup/keys with provider="google" — the
  // generic key-store endpoint that every other provider also uses.
  // On success the daemon's lifespan hydration (api/app.py:
  // _hydrate_secrets_into_env) re-exports these as env vars on the
  // NEXT restart, so we need to tell the user to restart for the
  // change to take effect.
  //
  // Why not just store + auto-hydrate without restart? The Settings
  // class uses pydantic-settings with an lru_cache — busted by
  // _hydrate_secrets_into_env at lifespan-start ONCE. Hot-reloading
  // mid-process would mean tearing down + re-bootstrapping every
  // provider that already cached the (formerly empty) Google client.
  // Cleaner to require a restart for now; auto-reload is a future
  // refinement.
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  async function submit() {
    setErr(null);
    setSavedMsg(null);
    const cid = clientId.trim();
    const csec = clientSecret.trim();
    if (!cid || !csec) {
      setErr("Both fields are required.");
      return;
    }
    setBusy(true);
    try {
      await api.setupSaveKeys("google", {
        oauth_client_id: cid,
        oauth_client_secret: csec,
      });
      // Status won't flip to configured=true until the daemon
      // restarts (see component docstring). Tell the user that
      // explicitly — silent UX of "saved but button still
      // disabled" would be confusing.
      setSavedMsg(
        "Saved. Restart the OpenVox daemon to load the new credentials, " +
          "then refresh this page.",
      );
      setClientId("");
      setClientSecret("");
      onSaved();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-4 space-y-3">
      <div>
        <div className="font-semibold text-sm mb-1">
          Google OAuth client — one-time maintainer setup
        </div>
        <div className="text-xs text-muted-foreground leading-relaxed">
          Paste the Client ID + Client Secret from your Google Cloud Console
          Desktop App OAuth client. Same client works for Gmail + Calendar +
          Contacts. See{" "}
          <a
            href="https://github.com/amznsri/openvox/blob/main/docs/integrations/google.md"
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-foreground"
          >
            docs/integrations/google.md
          </a>{" "}
          for the 2-minute Cloud Console walk-through. Stored encrypted at
          rest in <code>~/.openvox/openvox.db</code>; never leaves this
          machine.
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <Label className="text-xs">Client ID</Label>
          <Input
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder="123-abc.apps.googleusercontent.com"
            className="font-mono text-xs"
            autoComplete="off"
          />
        </div>
        <div>
          <Label className="text-xs">Client Secret</Label>
          <Input
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            placeholder="GOCSPX-..."
            className="font-mono text-xs"
            type="password"
            autoComplete="off"
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button onClick={submit} disabled={busy} className="gap-2" size="sm">
          {busy && <Loader2 className="h-3 w-3 animate-spin" />}
          Save credentials
        </Button>
        {savedMsg && (
          <div className="text-[11px] text-emerald-300 leading-snug">
            {savedMsg}
          </div>
        )}
        {err && (
          <div className="text-[11px] text-rose-300 leading-snug">{err}</div>
        )}
      </div>
    </div>
  );
}


function AccountRow({
  account,
  onDisconnect,
  focused,
}: {
  account: GoogleIntegrationAccount;
  onDisconnect: () => void;
  /** Set when arrived from the command palette's "Disconnect <email>"
   *  action. Triggers a scroll-into-view + a brief pulse so the user
   *  sees which row was deeplinked. Note: NEVER auto-clicks the
   *  Disconnect button — voice transcripts of email addresses are
   *  too lossy to trust on a destructive write. */
  focused?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const [pulsing, setPulsing] = useState(false);

  useEffect(() => {
    if (!focused || !rowRef.current) return;
    // Scroll the row into view + start the pulse. Pulse fades after
    // ~3s so a refresh later doesn't leave the row stuck highlighted.
    rowRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    setPulsing(true);
    const t = setTimeout(() => setPulsing(false), 3000);
    return () => clearTimeout(t);
  }, [focused]);

  const handleDisconnect = async () => {
    if (
      !confirm(
        `Disconnect ${account.user_email}? Agents using this account will lose access immediately.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.disconnectGoogleIntegration(account.user_email);
      onDisconnect();
    } catch (e: any) {
      setErr(e?.message || "Disconnect failed");
    } finally {
      setBusy(false);
    }
  };

  // Surface the most-relevant scopes (gmail / calendar / contacts) as
  // little badges. Full scopes list available on hover.
  const scopeBadges = scopeSummary(account.scopes);

  return (
    <div
      ref={rowRef}
      className={`rounded-md border p-3 transition-all ${
        pulsing
          ? "border-amber-400/80 bg-amber-500/10 ring-2 ring-amber-400/40"
          : "border-border/40 bg-background/30"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
            <div className="font-medium truncate">{account.user_email}</div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {scopeBadges.map((s) => (
              <Badge
                key={s.label}
                variant={s.granted ? "success" : "default"}
                className="text-[10px]"
              >
                {s.label}
              </Badge>
            ))}
          </div>
          {account.expires_at && (
            <div className="text-[11px] text-muted-foreground mt-2">
              Access token expires {prettyDate(account.expires_at)}{" "}
              (auto-refreshed; no action needed)
            </div>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleDisconnect}
          disabled={busy}
          className="gap-1.5"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Unplug className="h-3.5 w-3.5" />
          )}
          Disconnect
        </Button>
      </div>
      {err && (
        <div className="mt-2 text-xs text-red-400 flex items-center gap-1.5">
          <XCircle className="h-3 w-3" /> {err}
        </div>
      )}
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────


function scopeSummary(scopes: string[]): { label: string; granted: boolean }[] {
  // Map the most-relevant Google scopes to short, readable badges.
  // A scope is "granted" if it (or a broader equivalent) is present in
  // the granted-scopes list. Display badges for all expected ones so
  // the user can see at a glance what's missing.
  const has = (needle: string) => scopes.some((s) => s.includes(needle));
  return [
    { label: "Gmail", granted: has("gmail") },
    { label: "Calendar", granted: has("calendar") },
    { label: "Contacts", granted: has("contacts") },
  ];
}

function prettyDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    // Relative if within an hour, otherwise absolute date/time.
    const ms = d.getTime() - Date.now();
    if (Math.abs(ms) < 60_000) return "in <1 min";
    if (ms > 0 && ms < 3600_000) return `in ${Math.round(ms / 60_000)} min`;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}
