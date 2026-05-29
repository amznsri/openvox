"use client";

/**
 * Settings page — read AND edit provider credentials.
 *
 * History:
 *   Pre-v0.2.26 this page was read-only — provider rows showed a
 *   "missing key" / "configured" badge and the only path to add a
 *   key was either:
 *     (a) the first-run /dashboard/setup wizard (gated off as soon
 *         as ONE provider was configured), or
 *     (b) editing `~/.openvox/.env` and restarting the daemon.
 *
 *   That broke the post-install flow whenever an operator wanted to
 *   ADD a second provider — like pasting an OpenAI key for Realtime
 *   on top of an already-working BytePlus install (the actual S2S
 *   PR-B validation flow). User reported this concretely:
 *
 *     > "There is no option to add api key from Settings dashboard.
 *     >  Dashboard → Settings → paste your OpenAI key"
 *
 *   v0.2.26 (this commit) turns each provider row into an
 *   expandable inline form. Clicking a row reveals input(s)
 *   appropriate to that provider; Save POSTs to
 *   /api/v1/admin/setup/keys — the same endpoint the first-run
 *   wizard uses + the Integrations page uses for Google OAuth.
 *
 * Storage / hydration / restart
 *   - Saving writes to ~/.openvox/openvox.db's encrypted secret
 *     store via secrets.set_provider_key() (the daemon's lifespan
 *     handles encryption transparently).
 *   - On next daemon start (or via the lifespan rehydration that
 *     fires when a key changes), the key is exported into the
 *     matching env var (e.g. OPENAI_API_KEY).
 *   - The provider's `is_available()` re-evaluates and the row
 *     flips from "missing key" to "configured".
 *
 *   We surface "restart daemon" in the success toast so the
 *   operator knows when to expect the green badge.
 */

import { useState, useEffect } from "react";
import useSWR from "swr";
import {
  ShieldCheck,
  Database,
  Globe,
  Loader2,
  KeyRound,
  Pencil,
  Save,
  X,
  CheckCircle2,
} from "lucide-react";

import { api, type Provider } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";

/**
 * Per-provider key-entry spec.
 *
 * Maps a `Provider.id` → the `(provider, [fields])` shape the
 * backend's /admin/setup/keys endpoint expects.
 *
 * Two cases worth calling out:
 *
 *   1. `openai_realtime` (S2S — Phase 3 PR-B) shares storage with
 *      `openai` (same OpenAI API key serves LLM + TTS + Realtime).
 *      Both rows map to provider="openai", so pasting the key in
 *      either spot flips all three rows green simultaneously.
 *
 *   2. `byteplus` carries TWO keys (separate LLM + Voice accounts
 *      on the BytePlus side). All BytePlus rows (LLM / STT / TTS /
 *      RTC) collapse to the same entry — though RTC needs the
 *      extra app_id / app_key pair which we surface as a
 *      "show advanced" tail.
 *
 *   3. Providers NOT in this map render as read-only (badge only,
 *      no edit pencil). Today that's `whisper` (local-only, no
 *      key needed) and any third-party plugin provider that
 *      hasn't told us about its key shape.
 */
type FieldSpec = {
  key: string;
  label: string;
  placeholder?: string;
  /** When true, the input is type="password" so keys aren't
   *  shoulder-readable. Default true for `api_key` shaped fields. */
  secret?: boolean;
};

type KeySpec = {
  /** Slot in the secret store. Multiple Provider.id values can
   *  map to the same backend slot (openai + openai_realtime → "openai"). */
  storage_provider: string;
  fields: FieldSpec[];
  /** Extra rendered note, e.g. for openai_realtime explaining the
   *  shared-key situation. */
  note?: string;
};

const KEY_SPEC_BY_PROVIDER_ID: Record<string, KeySpec> = {
  byteplus: {
    storage_provider: "byteplus",
    fields: [
      { key: "llm_api_key", label: "LLM API key", placeholder: "01a2b3c4-…", secret: true },
      { key: "voice_api_key", label: "Voice API key (TTS + STT)", placeholder: "01a2b3c4-…", secret: true },
      // RTC fields are optional — surface them as well; the
      // backend tolerates empty values (a blank key means "skip
      // this slot").
      { key: "rtc_app_id", label: "RTC App ID (optional)", placeholder: "" },
      { key: "rtc_app_key", label: "RTC App key (optional)", placeholder: "", secret: true },
    ],
    note: "BytePlus uses separate accounts for LLM vs Voice. RTC fields are only needed if you connect via BytePlus RTC.",
  },
  openai: {
    storage_provider: "openai",
    fields: [{ key: "api_key", label: "OpenAI API key", placeholder: "sk-…", secret: true }],
    note: "Same key powers OpenAI LLM, TTS, and Realtime (S2S).",
  },
  openai_realtime: {
    storage_provider: "openai",
    fields: [{ key: "api_key", label: "OpenAI API key", placeholder: "sk-…", secret: true }],
    note: "Reuses your OpenAI API key — pasting here also enables OpenAI LLM + TTS.",
  },
  anthropic: {
    storage_provider: "anthropic",
    fields: [{ key: "api_key", label: "Anthropic API key", placeholder: "sk-ant-…", secret: true }],
  },
  gemini: {
    storage_provider: "gemini",
    fields: [{ key: "api_key", label: "Gemini API key", secret: true }],
  },
  deepseek: {
    storage_provider: "deepseek",
    fields: [{ key: "api_key", label: "DeepSeek API key", secret: true }],
  },
  elevenlabs: {
    storage_provider: "elevenlabs",
    fields: [{ key: "api_key", label: "ElevenLabs API key", secret: true }],
  },
  cartesia: {
    storage_provider: "cartesia",
    fields: [{ key: "api_key", label: "Cartesia API key", secret: true }],
  },
  deepgram: {
    storage_provider: "deepgram",
    fields: [{ key: "api_key", label: "Deepgram API key", secret: true }],
  },
  assemblyai: {
    storage_provider: "assemblyai",
    fields: [{ key: "api_key", label: "AssemblyAI API key", secret: true }],
  },
};

export default function SettingsPage() {
  const { data: providers = [], isLoading, mutate } = useSWR<Provider[]>(
    "providers",
    () => api.listProviders(),
  );

  // Which row is currently expanded for editing. `null` = none.
  // We allow at most one open at a time so the page doesn't
  // turn into a stack of half-filled forms.
  const [editingId, setEditingId] = useState<string | null>(null);
  // Last-saved row, used to show a brief "saved — restart daemon"
  // hint inline so the operator knows what to expect.
  const [recentlySaved, setRecentlySaved] = useState<string | null>(null);

  // The wheel daemon serves the dashboard AND the API on the same
  // origin — and that origin is NOT always :8000 (the daemon
  // auto-switches when 8000 is busy). Read the live origin on mount so
  // the Networking card shows the real URL instead of a hard-coded
  // :8000 that would be wrong on an auto-switched port. Set in an
  // effect (not at render) to avoid an SSR/prerender hydration
  // mismatch — `window` doesn't exist during static export.
  const [origin, setOrigin] = useState<string>("");
  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  return (
    <div className="container py-8 space-y-6 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground text-sm">
          Click any provider to add or update its credentials. Keys are
          encrypted at rest in <code className="text-foreground">~/.openvox/openvox.db</code>{" "}
          with a per-host key in{" "}
          <code className="text-foreground">~/.openvox/secret.key</code>; a
          daemon restart picks up changes via the lifespan hydration step.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-violet-300" />
            Provider credentials
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1">
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : (
            providers.map((p) => {
              const rowId = `${p.type}.${p.id}`;
              const spec = KEY_SPEC_BY_PROVIDER_ID[p.id];
              const editable = spec !== undefined;
              const isOpen = editingId === rowId;
              return (
                <ProviderRow
                  key={rowId}
                  provider={p}
                  rowId={rowId}
                  spec={spec}
                  editable={editable}
                  isOpen={isOpen}
                  recentlySaved={recentlySaved === rowId}
                  onToggle={() =>
                    setEditingId((cur) => (cur === rowId ? null : rowId))
                  }
                  onSaved={() => {
                    setEditingId(null);
                    setRecentlySaved(rowId);
                    // SWR-refresh the provider list so `available`
                    // flips from false → true wherever the new key
                    // resolves. (openai_realtime / openai both flip
                    // when the OpenAI api_key lands.)
                    void mutate();
                    // Auto-clear the "saved" hint after a few
                    // seconds so the page doesn't accumulate stale
                    // affirmations across multiple saves.
                    setTimeout(() => setRecentlySaved(null), 8000);
                  }}
                />
              );
            })
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-emerald-300" />
            Privacy
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm space-y-2">
          <SettingRow label="Auth" value="OPENVOX_AUTH=disabled (local-first)" />
          <SettingRow label="Audio storage" value="ENABLE_AUDIO_STORAGE=false" hint="Off by default — GDPR-friendly." />
          <SettingRow label="Transcript storage" value="ENABLE_TRANSCRIPT_STORAGE=true" />
          <SettingRow label="Retention" value="DATA_RETENTION_DAYS=30" />
          <SettingRow label="PII masking" value="PII_MASKING_ENABLED=true" />
          <SettingRow label="Residency region" value="DATA_RESIDENCY_REGION=ap-southeast-1" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="h-4 w-4 text-cyan-300" />
            Storage backend
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm space-y-2">
          <SettingRow label="Backend" value="STORAGE_BACKEND=local | byteplus_tos | s3 | gcs | alibaba_oss" />
          <SettingRow label="Local path" value="STORAGE_LOCAL_PATH=./.openvox/storage" />
          <SettingRow label="BytePlus TOS endpoint" value="tos-ap-southeast-1.bytepluses.com" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-amber-300" />
            Networking
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm space-y-2">
          {/* The daemon serves the dashboard + API + WebSocket on this
              one origin. We show it live (rather than a hard-coded
              :8000) because the daemon auto-switches ports when 8000 is
              busy. The Node gateway row was removed — that process was
              deleted in Phase 1; the dashboard talks to core directly. */}
          <SettingRow label="Core API / Dashboard" value={origin || "(loading…)"} />
          <SettingRow label="WebSocket" value={origin ? origin.replace(/^http/, "ws") : "(loading…)"} />
        </CardContent>
      </Card>
    </div>
  );
}

function ProviderRow({
  provider,
  rowId,
  spec,
  editable,
  isOpen,
  recentlySaved,
  onToggle,
  onSaved,
}: {
  provider: Provider;
  rowId: string;
  spec: KeySpec | undefined;
  editable: boolean;
  isOpen: boolean;
  recentlySaved: boolean;
  onToggle: () => void;
  onSaved: () => void;
}) {
  return (
    <div className="rounded-md hover:bg-muted/30">
      {/* Header row — always visible. Clickable iff editable. */}
      <div
        className={`flex items-center justify-between py-2 px-3 ${
          editable ? "cursor-pointer" : ""
        }`}
        onClick={editable ? onToggle : undefined}
        role={editable ? "button" : undefined}
        aria-expanded={editable ? isOpen : undefined}
      >
        <div className="flex items-center gap-3">
          <Badge variant="default" className="font-mono">
            {provider.type}
          </Badge>
          <div className="text-sm">{provider.display_name}</div>
        </div>
        <div className="flex items-center gap-2">
          {recentlySaved && (
            <span className="text-[11px] text-emerald-300 inline-flex items-center gap-1">
              <CheckCircle2 className="h-3 w-3" /> saved — restart daemon
            </span>
          )}
          <Badge variant={provider.available ? "success" : "default"}>
            {provider.available ? "configured" : "missing key"}
          </Badge>
          {editable && (
            <Pencil
              className={`h-3.5 w-3.5 transition-colors ${
                isOpen ? "text-violet-300" : "text-muted-foreground"
              }`}
            />
          )}
        </div>
      </div>

      {/* Edit form — rendered inline below the row when open. */}
      {isOpen && spec && (
        <ProviderKeyForm spec={spec} onCancel={onToggle} onSaved={onSaved} />
      )}
    </div>
  );
}

function ProviderKeyForm({
  spec,
  onCancel,
  onSaved,
}: {
  spec: KeySpec;
  onCancel: () => void;
  onSaved: () => void;
}) {
  // Form state — one value per field key. We never pre-fill from
  // the server (a) because the API doesn't expose existing values,
  // and (b) because pre-filling a password input is a phishing
  // attractant. Blank = unchanged on submit IF the spec defaults to
  // empty-skip; but `/admin/setup/keys` interprets an empty key as
  // "delete this slot", so we explicitly omit fields that the user
  // left blank from the POST payload below.
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(spec.fields.map((f) => [f.key, ""])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      // Trim values. Strip empties so the backend doesn't interpret
      // a blank input as "delete this slot" — the user might just
      // have left the optional RTC fields alone.
      const payload: Record<string, string> = {};
      for (const f of spec.fields) {
        const v = (values[f.key] || "").trim();
        if (v) payload[f.key] = v;
      }
      if (Object.keys(payload).length === 0) {
        throw new Error("Enter at least one key before saving.");
      }
      await api.setupSaveKeys(spec.storage_provider, payload);
      onSaved();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="px-3 pb-3 pt-1 space-y-3 border-t border-border/40 ml-3 mr-3 mb-2 mt-1 rounded-b-md bg-muted/20">
      {spec.note && (
        <p className="text-[11px] text-muted-foreground pt-2">{spec.note}</p>
      )}
      <div className="grid grid-cols-1 gap-2.5">
        {spec.fields.map((f) => (
          <div key={f.key}>
            <Label className="text-xs">{f.label}</Label>
            <Input
              type={f.secret !== false ? "password" : "text"}
              placeholder={f.placeholder || ""}
              value={values[f.key] || ""}
              onChange={(e) =>
                setValues((cur) => ({ ...cur, [f.key]: e.target.value }))
              }
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
          </div>
        ))}
      </div>
      {error && (
        <div className="text-xs text-rose-300">{error}</div>
      )}
      <div className="flex items-center gap-2 justify-end pt-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={busy}
        >
          <X className="h-3.5 w-3.5" />
          Cancel
        </Button>
        <Button
          variant="gradient"
          size="sm"
          onClick={save}
          disabled={busy}
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          Save key
        </Button>
      </div>
    </div>
  );
}

function SettingRow({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-border/40 last:border-0">
      <div className="text-muted-foreground">{label}</div>
      <div className="text-right">
        <div className="font-mono text-xs">{value}</div>
        {hint && <div className="text-[11px] text-muted-foreground mt-0.5">{hint}</div>}
      </div>
    </div>
  );
}
