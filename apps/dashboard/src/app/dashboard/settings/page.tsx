"use client";

import useSWR from "swr";
import { ShieldCheck, Database, Globe, Loader2, KeyRound } from "lucide-react";

import { api, type Provider } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function SettingsPage() {
  const { data: providers = [], isLoading } = useSWR<Provider[]>("providers", () =>
    api.listProviders(),
  );

  const groups: Record<string, Provider[]> = {};
  for (const p of providers) (groups[p.type] = groups[p.type] || []).push(p);

  return (
    <div className="container py-8 space-y-6 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground text-sm">
          OpenVox is configured via your <code className="text-foreground">.env</code> file.
          Restart the stack with <code className="text-foreground">docker compose up</code> after
          changing values.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-violet-300" />
            Provider credentials
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : (
            providers.map((p) => (
              <div
                key={`${p.type}.${p.id}`}
                className="flex items-center justify-between py-2 px-3 rounded-md hover:bg-muted/30"
              >
                <div className="flex items-center gap-3">
                  <Badge variant="default" className="font-mono">
                    {p.type}
                  </Badge>
                  <div className="text-sm">{p.display_name}</div>
                </div>
                <Badge variant={p.available ? "success" : "default"}>
                  {p.available ? "configured" : "missing key"}
                </Badge>
              </div>
            ))
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
          <SettingRow label="Core API" value="http://localhost:8000" />
          <SettingRow label="Gateway (Node)" value="http://localhost:3001" />
          <SettingRow label="Dashboard" value="http://localhost:3000" />
        </CardContent>
      </Card>
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
