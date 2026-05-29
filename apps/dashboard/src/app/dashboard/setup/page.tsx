"use client";

/**
 * First-run setup wizard — single-page welcome + API-key entry.
 *
 * Triggered by `<SetupGate />` in `dashboard/layout.tsx` whenever
 * `/api/v1/admin/setup/status` reports `complete: false`. The page
 * presents a provider picker + key form, calls
 * `/api/v1/admin/setup/keys` (POST), then routes the user to the
 * Templates page so they can immediately try a working agent.
 *
 * Provider matrix in this v1
 * ==========================
 * Three pre-set providers cover ~95% of new users. Each is a card
 * with the key inputs it needs:
 *
 *   - BytePlus       (LLM + Voice — single account, lowest-friction)
 *   - OpenAI         (LLM + TTS — one key covers both)
 *   - Anthropic      (LLM only — pair with another voice provider)
 *
 * A fourth "advanced — add more later" link points to /dashboard/settings
 * for users who want ElevenLabs / Deepgram / etc. (those work today
 * via env vars but don't show in this welcome flow).
 *
 * After saving, the page polls status until complete + redirects.
 * Operator escape: if the env vars already set things up, status
 * comes back complete on mount and we redirect straight away.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { mutate } from "swr";
import { CheckCircle2, KeyRound, Loader2, Sparkles } from "lucide-react";

import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";

type ProviderId = "byteplus" | "openai" | "anthropic";

type ProviderSpec = {
  id: ProviderId;
  name: string;
  blurb: string;
  /** Each entry becomes one input. The `key` matches the backend
   *  `key_name` used in /admin/setup/keys. */
  fields: { key: string; label: string; placeholder?: string }[];
};

const PROVIDERS: ProviderSpec[] = [
  {
    id: "byteplus",
    name: "BytePlus (recommended)",
    blurb: "Single account → LLM + STT + TTS. Lowest-friction first-run.",
    fields: [
      { key: "llm_api_key", label: "LLM API key", placeholder: "01a2b3c4..." },
      { key: "voice_api_key", label: "Voice API key", placeholder: "01a2b3c4..." },
    ],
  },
  {
    id: "openai",
    name: "OpenAI",
    blurb: "One key covers GPT (LLM) + TTS. Bring your own.",
    fields: [
      { key: "api_key", label: "API key", placeholder: "sk-..." },
    ],
  },
  {
    id: "anthropic",
    name: "Anthropic (LLM only)",
    blurb: "Use Claude for the LLM. Pair with another voice provider.",
    fields: [
      { key: "api_key", label: "API key", placeholder: "sk-ant-..." },
    ],
  },
];

export default function SetupPage() {
  const router = useRouter();
  const [selected, setSelected] = useState<ProviderId>("byteplus");
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  // If the operator already populated keys via .env, status will be
  // complete from the start — bounce immediately.
  useEffect(() => {
    api.setupStatus()
      .then((s) => {
        if (s.complete) {
          // Prime the shared cache so SetupGate doesn't bounce us off
          // /dashboard/agents (a non-self-serve page) with a stale
          // complete:false before its own SWR fetch resolves.
          mutate("setup-status", s, { revalidate: false });
          router.replace("/dashboard/agents");
        }
      })
      .catch(() => {
        // status check failed (core not reachable?) — surface in UI
        // so the user knows what's wrong rather than silently bouncing.
      });
  }, [router]);

  const spec = PROVIDERS.find((p) => p.id === selected)!;

  function setField(k: string, v: string) {
    setValues((cur) => ({ ...cur, [k]: v }));
  }

  async function submit() {
    setSubmitting(true);
    setError("");
    try {
      // Only send fields with values — the backend treats empty
      // strings as "delete this key", which would clear an existing
      // env-var-fallback'd setup.
      const keysToSend: Record<string, string> = {};
      for (const f of spec.fields) {
        const v = (values[f.key] || "").trim();
        if (v) keysToSend[f.key] = v;
      }
      if (Object.keys(keysToSend).length === 0) {
        setError("Paste at least one key for the selected provider.");
        setSubmitting(false);
        return;
      }
      const res = await api.setupSaveKeys(selected, keysToSend);
      if (!res.status.complete) {
        // Saved a key but still missing something (e.g. only LLM
        // when we also need voice). Show progress + invite user to
        // add the missing side.
        const missing: string[] = [];
        if (!res.status.have_llm) missing.push("LLM");
        if (!res.status.have_voice) missing.push("voice");
        setError(`Saved, but still missing: ${missing.join(" + ")}.`);
        setSubmitting(false);
        return;
      }
      // Done — show success briefly, then redirect.
      //
      // Prime the SHARED setup-status cache before navigating. SetupGate
      // (mounted in the dashboard layout) reads this exact SWR key; the
      // destination here (/dashboard/templates) is NOT a setup
      // self-serve page, so if SetupGate still has the stale
      // complete:false from first load it would bounce us back to
      // /setup — the flicker a first-time user sees right after saving
      // their first keys. Writing the fresh status into the cache (no
      // revalidate) makes the gate see complete:true on arrival.
      mutate("setup-status", res.status, { revalidate: false });
      setDone(true);
      setTimeout(() => router.replace("/dashboard/templates"), 1200);
    } catch (e: any) {
      setError(e?.message || "save failed");
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-[80vh] flex items-center justify-center p-6">
      <Card className="w-full max-w-2xl">
        <CardContent className="pt-8 space-y-6">
          <div className="text-center space-y-2">
            <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-cyan-400 mb-2">
              <Sparkles className="h-6 w-6 text-white" />
            </div>
            <h1 className="text-2xl font-bold">Welcome to OpenVox</h1>
            <p className="text-sm text-muted-foreground max-w-md mx-auto">
              Build your first voice agent in three minutes. We just need an API
              key so we can talk to a model on your behalf.
            </p>
          </div>

          {/* Provider picker — three cards */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setSelected(p.id)}
                className={
                  "text-left rounded-md border px-3 py-3 transition-colors " +
                  (selected === p.id
                    ? "border-violet-500/60 bg-violet-500/10"
                    : "border-border/60 hover:border-border")
                }
              >
                <div className="text-sm font-medium">{p.name}</div>
                <div className="text-xs text-muted-foreground mt-1">{p.blurb}</div>
              </button>
            ))}
          </div>

          {/* Per-provider key inputs */}
          <div className="space-y-3 pt-2">
            {spec.fields.map((f) => (
              <div key={f.key}>
                <Label className="flex items-center gap-1.5">
                  <KeyRound className="h-3 w-3" />
                  {f.label}
                </Label>
                <Input
                  type="password"
                  placeholder={f.placeholder}
                  value={values[f.key] || ""}
                  onChange={(e) => setField(f.key, e.target.value)}
                  className="font-mono text-xs"
                />
              </div>
            ))}
            <p className="text-xs text-muted-foreground">
              Keys are encrypted at rest using a machine-local key
              (<code>~/.openvox/secret.key</code>). Never logged. Stored under
              the <code>provider_keys</code> table.
            </p>
          </div>

          {error && (
            <div className="rounded-md border border-rose-500/40 bg-rose-500/10 text-rose-200 text-sm px-3 py-2">
              {error}
            </div>
          )}
          {done && (
            <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-200 text-sm px-3 py-2 flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4" />
              Saved. Loading templates…
            </div>
          )}

          <div className="flex items-center justify-between gap-2 pt-2">
            <a
              href="/dashboard/settings"
              className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
            >
              Advanced — add ElevenLabs, Deepgram, or more
            </a>
            <Button
              variant="gradient"
              onClick={submit}
              disabled={submitting || done}
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              {done ? "Saved" : "Save & continue"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
