"use client";

import Link from "next/link";
import {
  Mic,
  Github,
  Bot,
  Globe,
  Plug,
  ShieldCheck,
  ArrowRight,
  Zap,
  Sparkles,
  PhoneCall,
  Activity,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const providers = [
  "BytePlus Seed-2.0",
  "ElevenLabs",
  "Deepgram",
  "OpenAI GPT-4o",
  "Anthropic Claude",
  "Google Gemini",
  "DeepSeek",
  "Cartesia",
  "AssemblyAI",
  "Twilio",
];

const useCases = [
  {
    icon: Bot,
    title: "E-commerce support",
    desc: "Order lookups, returns, stock checks. Voice-first.",
  },
  {
    icon: Sparkles,
    title: "Education tutor",
    desc: "Science and math, with worked examples.",
  },
  {
    icon: Activity,
    title: "Stock analyst",
    desc: "Live quotes and indicator-driven analysis.",
  },
  {
    icon: Mic,
    title: "Voice analyzer",
    desc: "Sentiment, profanity, and call-quality QA.",
  },
];

const features = [
  {
    icon: Zap,
    title: "Sub-300ms latency",
    desc: "Sentence-level streaming pipeline keeps the conversation fluid.",
  },
  {
    icon: Plug,
    title: "Pluggable providers",
    desc: "BytePlus, ElevenLabs, Deepgram, OpenAI, Anthropic — swap any layer at any time.",
  },
  {
    icon: PhoneCall,
    title: "Web, phone, and chat",
    desc: "Browser RTC, Twilio voice calls, WhatsApp Business, Telegram. One agent, every channel.",
  },
  {
    icon: ShieldCheck,
    title: "Local-first by default",
    desc: "Runs on your laptop. SQLite + filesystem. Your audio never leaves your machine.",
  },
  {
    icon: Globe,
    title: "GDPR-aware",
    desc: "Configurable retention, regional residency, transcript-only mode, PII masking.",
  },
  {
    icon: Sparkles,
    title: "Skills & extensions",
    desc: "Add tools by dropping a Python file in ~/.openvox/skills, or pip-install third-party packages.",
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen flex flex-col">
      {/* Top nav */}
      <nav className="sticky top-0 z-50 border-b border-border/60 bg-background/70 backdrop-blur-xl">
        <div className="container flex items-center justify-between h-16">
          <Link href="/" className="flex items-center gap-2">
            <div className="relative">
              <div className="h-7 w-7 rounded-md bg-gradient-to-br from-violet-500 to-cyan-400 flex items-center justify-center">
                <Mic className="h-4 w-4 text-white" />
              </div>
              <div className="absolute inset-0 rounded-md bg-gradient-to-br from-violet-500 to-cyan-400 blur-md opacity-40" />
            </div>
            <span className="text-base font-bold">OpenVox</span>
            <Badge variant="primary" className="ml-1">v0.1</Badge>
          </Link>
          <div className="flex items-center gap-4 text-sm">
            <a href="#features" className="text-muted-foreground hover:text-foreground transition-colors">
              Features
            </a>
            <a href="#templates" className="text-muted-foreground hover:text-foreground transition-colors">
              Templates
            </a>
            <a href="#stack" className="text-muted-foreground hover:text-foreground transition-colors">
              Providers
            </a>
            <a
              href="https://github.com/openvox/openvox"
              target="_blank"
              rel="noreferrer"
              className="hidden md:inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
            >
              <Github className="h-4 w-4" /> GitHub
            </a>
            <Link href="/dashboard">
              <Button variant="gradient" size="sm">
                Open dashboard
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 -z-10 pointer-events-none">
          <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[60rem] h-[60rem] rounded-full bg-gradient-to-br from-violet-500/20 via-fuchsia-500/10 to-cyan-400/10 blur-3xl" />
        </div>
        <div className="container py-24 md:py-32 text-center">
          <Badge variant="primary" className="mb-6 mx-auto">
            <span className="h-1.5 w-1.5 rounded-full bg-violet-400 mr-1.5 animate-pulse" />
            Open-source • Local-first • Apache-2.0
          </Badge>
          <h1 className="text-5xl md:text-7xl font-bold tracking-tight">
            Voice agents that <span className="gradient-text">actually ship.</span>
          </h1>
          <p className="mt-6 text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto">
            OpenVox is the open platform for building, testing, and deploying production-grade voice
            agents. Every layer is swappable. Every byte stays on your machine — until you say otherwise.
          </p>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
            <Link href="/dashboard/agents/new?mode=voice">
              <Button variant="gradient" size="lg">
                <Mic className="h-4 w-4" />
                Build by voice
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
            <Link href="/dashboard">
              <Button variant="outline" size="lg">
                Open the dashboard
              </Button>
            </Link>
            <Link href="/dashboard/playground">
              <Button variant="ghost" size="lg">
                Try the playground
              </Button>
            </Link>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            🎙 Talk to the Setup Assistant — it&apos;ll build your first agent
            for you. Or hop straight to the dashboard.
          </p>

          <div className="mt-12 flex flex-wrap items-center justify-center gap-3 text-xs text-muted-foreground">
            <span className="font-medium tracking-wide uppercase">Powered by</span>
            {providers.map((p) => (
              <span key={p} className="px-2.5 py-1 rounded-full border border-border/60 bg-card/40">
                {p}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* Use cases */}
      <section id="templates" className="container py-16 md:py-24">
        <div className="text-center mb-12">
          <h2 className="text-3xl md:text-4xl font-bold">Pre-built templates, ready to launch</h2>
          <p className="mt-3 text-muted-foreground">
            Four production-quality blueprints. Customise the prompt, plug your skills, ship.
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {useCases.map((u) => (
            <div key={u.title} className="gradient-border rounded-xl p-5 hover:translate-y-[-2px] transition-transform">
              <u.icon className="h-6 w-6 text-violet-400 mb-3" />
              <h3 className="font-semibold">{u.title}</h3>
              <p className="text-sm text-muted-foreground mt-1">{u.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section id="features" className="container py-16 md:py-24">
        <div className="text-center mb-12">
          <h2 className="text-3xl md:text-4xl font-bold">Everything you need, none of the lock-in</h2>
          <p className="mt-3 text-muted-foreground max-w-2xl mx-auto">
            Pick the providers that fit. Build skills in plain Python. Deploy to a single laptop or
            a kubernetes cluster — same code path.
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((f) => (
            <div
              key={f.title}
              className="rounded-xl p-6 border border-border/60 bg-card/50 backdrop-blur-xl hover:border-primary/40 transition-colors"
            >
              <f.icon className="h-5 w-5 text-cyan-400 mb-3" />
              <h3 className="font-semibold">{f.title}</h3>
              <p className="text-sm text-muted-foreground mt-1.5">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Stack */}
      <section id="stack" className="container py-16 md:py-24">
        <div className="rounded-2xl gradient-border p-8 md:p-12 text-center">
          <h2 className="text-3xl font-bold">One config. Every provider.</h2>
          <p className="mt-3 text-muted-foreground max-w-2xl mx-auto">
            <code className="px-1.5 py-0.5 rounded bg-muted text-foreground text-sm">.env</code>{" "}
            files supply credentials. Swap providers per-agent at runtime — even mid-call.
          </p>
          <div className="mt-8 grid grid-cols-3 md:grid-cols-6 gap-3 text-xs">
            {[
              "BytePlus", "OpenAI", "Anthropic", "Gemini", "DeepSeek",
              "ElevenLabs", "Deepgram", "AssemblyAI", "Cartesia", "Whisper",
              "Twilio", "WhatsApp",
            ].map((s) => (
              <div
                key={s}
                className="px-3 py-2 rounded-md border border-border/60 bg-card/40 hover:bg-card/70 transition-colors"
              >
                {s}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="container py-16">
        <div className="text-center rounded-2xl py-16 px-6 bg-gradient-to-br from-violet-500/10 via-card to-cyan-400/10 border border-border/60">
          <h2 className="text-3xl md:text-4xl font-bold">Ready to build?</h2>
          <p className="mt-3 text-muted-foreground">
            Run <code className="px-1.5 py-0.5 rounded bg-muted text-foreground text-sm">docker compose up</code> and you're live.
          </p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Link href="/dashboard">
              <Button variant="gradient" size="lg">
                Open dashboard
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
            <a href="https://github.com/openvox/openvox" target="_blank" rel="noreferrer">
              <Button variant="outline" size="lg">
                <Github className="h-4 w-4" />
                Read the docs
              </Button>
            </a>
          </div>
        </div>
      </section>

      <footer className="border-t border-border/60 mt-16">
        <div className="container py-8 flex flex-col md:flex-row items-center justify-between gap-4 text-sm text-muted-foreground">
          <p>OpenVox — Apache-2.0. Built openly.</p>
          <div className="flex items-center gap-4">
            <a href="https://github.com/openvox/openvox" className="hover:text-foreground" target="_blank" rel="noreferrer">
              GitHub
            </a>
            <Link href="/dashboard" className="hover:text-foreground">Dashboard</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
