"use client";

import Link from "next/link";
import { Bell, Search, Plus, ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";

export function Topbar({ title }: { title?: string }) {
  return (
    <header className="h-16 flex items-center justify-between gap-3 px-6 border-b border-border/60 bg-background/40 backdrop-blur-xl">
      <div className="flex items-center gap-3">
        {title && <h1 className="text-base font-semibold">{title}</h1>}
      </div>
      <div className="flex-1 max-w-md mx-auto hidden md:block">
        <div className="relative">
          <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="Search agents, templates, skills…"
            className="w-full h-9 rounded-md bg-input/40 border border-border/60 pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Link href="/dashboard/agents/new">
          <Button variant="gradient" size="sm">
            <Plus className="h-4 w-4" />
            New agent
          </Button>
        </Link>
        <Button variant="ghost" size="icon" aria-label="Notifications">
          <Bell className="h-4 w-4" />
        </Button>
        <button className="flex items-center gap-2 h-9 px-2 rounded-md hover:bg-muted">
          <div className="h-7 w-7 rounded-full bg-gradient-to-br from-violet-500 to-cyan-400" />
          <span className="text-sm hidden md:inline">Local user</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
      </div>
    </header>
  );
}
