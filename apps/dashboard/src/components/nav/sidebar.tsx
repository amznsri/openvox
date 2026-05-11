"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Bot,
  Sparkles,
  Wand2,
  Plug,
  BarChart3,
  Settings,
  Mic,
  Github,
} from "lucide-react";

import { cn } from "@/lib/utils";

const sections = [
  {
    items: [
      { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
      { href: "/dashboard/playground", label: "Playground", icon: Mic },
    ],
  },
  {
    title: "Build",
    items: [
      { href: "/dashboard/agents", label: "Agents", icon: Bot },
      { href: "/dashboard/templates", label: "Templates", icon: Sparkles },
      { href: "/dashboard/skills", label: "Skills", icon: Wand2 },
    ],
  },
  {
    title: "Operate",
    items: [
      { href: "/dashboard/providers", label: "Providers", icon: Plug },
      { href: "/dashboard/observability", label: "Observability", icon: BarChart3 },
      { href: "/dashboard/settings", label: "Settings", icon: Settings },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="hidden md:flex w-64 shrink-0 flex-col border-r border-border/60 bg-background/40 backdrop-blur-xl">
      <Link href="/" className="flex items-center gap-2 px-6 h-16 border-b border-border/60">
        <div className="relative">
          <div className="h-7 w-7 rounded-md bg-gradient-to-br from-violet-500 to-cyan-400 flex items-center justify-center">
            <Mic className="h-4 w-4 text-white" />
          </div>
          <div className="absolute inset-0 rounded-md bg-gradient-to-br from-violet-500 to-cyan-400 blur-md opacity-40" />
        </div>
        <div>
          <div className="text-base font-bold leading-none">OpenVox</div>
          <div className="text-[10px] text-muted-foreground tracking-widest uppercase">
            Voice agents
          </div>
        </div>
      </Link>

      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-6">
        {sections.map((sec, idx) => (
          <div key={idx}>
            {sec.title && (
              <div className="px-3 pb-2 text-[10px] font-semibold tracking-widest text-muted-foreground uppercase">
                {sec.title}
              </div>
            )}
            <ul className="space-y-0.5">
              {sec.items.map((item) => {
                const active =
                  pathname === item.href ||
                  (item.href !== "/dashboard" && pathname.startsWith(item.href));
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={cn(
                        "group flex items-center gap-3 px-3 h-9 rounded-md text-sm transition-colors",
                        active
                          ? "bg-primary/10 text-foreground border border-primary/30"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground",
                      )}
                    >
                      <Icon className={cn("h-4 w-4", active && "text-violet-300")} />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="p-3 border-t border-border/60">
        <a
          href="https://github.com/openvox/openvox"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-3 px-3 h-9 text-xs text-muted-foreground hover:text-foreground rounded-md hover:bg-muted"
        >
          <Github className="h-4 w-4" />
          Star on GitHub
        </a>
      </div>
    </aside>
  );
}
