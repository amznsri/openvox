#!/usr/bin/env node
/**
 * OpenVox CLI — manage agents, run dev sessions, scaffold skills.
 *
 * Talks to the local Node gateway (defaults to http://localhost:3001).
 *
 * Usage:
 *   openvox agents list
 *   openvox agents create --name "Support" --template ecommerce-support
 *   openvox templates list
 *   openvox skills new my_skill
 *   openvox status
 */
import { Command } from "commander";
import { bgGreen, dim, green, red, yellow } from "kolorist";
import { promises as fs } from "node:fs";
import path from "node:path";

const BASE = process.env.OPENVOX_API_URL || "http://localhost:3001";

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init.headers as any) },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text().catch(() => "")}`);
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

const program = new Command();
program
  .name("openvox")
  .description("OpenVox — local-first voice agent platform")
  .version("0.1.0");

// ── status ─────────────────────────────────────────────────────────
program
  .command("status")
  .description("Check the local stack")
  .action(async () => {
    try {
      const r = await api<{ status: string; version: string }>("/health");
      console.log(green(`✓ OpenVox gateway is up`), dim(`(v${r.version})`));
    } catch {
      console.log(red(`✗ Cannot reach ${BASE}. Is docker compose running?`));
      process.exit(1);
    }
  });

// ── agents ─────────────────────────────────────────────────────────
const agents = program.command("agents").description("Manage agents");

agents
  .command("list")
  .description("List all agents")
  .action(async () => {
    const list = await api<any[]>("/api/v1/agents");
    if (list.length === 0) return console.log(dim("(no agents — try `openvox templates list`)"));
    for (const a of list) {
      console.log(
        `${green(a.id.slice(0, 8))}  ${a.name.padEnd(28)}  ${dim(a.llm_provider)} ${dim(a.tts_provider)}  ${a.status === "published" ? bgGreen(" live ") : yellow("draft")}`,
      );
    }
  });

agents
  .command("create")
  .description("Create a new agent (optionally from a template)")
  .option("--name <name>", "Agent name")
  .option("--template <id>", "Template id (e.g. ecommerce-support)")
  .action(async (opts: { name?: string; template?: string }) => {
    if (opts.template) {
      const a = await api<any>(`/api/v1/templates/${opts.template}/instantiate`, {
        method: "POST",
        body: JSON.stringify({ name: opts.name }),
      });
      console.log(green(`✓ created ${a.id}`), `(${a.name})`);
    } else {
      const a = await api<any>("/api/v1/agents", {
        method: "POST",
        body: JSON.stringify({ name: opts.name || "Untitled agent" }),
      });
      console.log(green(`✓ created ${a.id}`), `(${a.name})`);
    }
  });

agents
  .command("delete <id>")
  .description("Delete an agent")
  .action(async (id: string) => {
    await api<void>(`/api/v1/agents/${id}`, { method: "DELETE" });
    console.log(green("✓ deleted"));
  });

// ── templates ──────────────────────────────────────────────────────
const templates = program.command("templates").description("Manage templates");

templates
  .command("list")
  .description("List built-in templates")
  .action(async () => {
    const list = await api<any[]>("/api/v1/templates");
    for (const t of list) {
      console.log(`${green(t.id.padEnd(22))} ${t.name}`);
      console.log(`  ${dim(t.tagline)}`);
    }
  });

// ── providers ──────────────────────────────────────────────────────
program
  .command("providers")
  .description("List configured providers")
  .action(async () => {
    const list = await api<any[]>("/api/v1/providers");
    for (const p of list) {
      const status = p.available ? green("ready") : dim("missing key");
      console.log(`${(p.type as string).padEnd(5)} ${p.id.padEnd(14)} ${p.display_name.padEnd(28)} ${status}`);
    }
  });

// ── skills new <name> ──────────────────────────────────────────────
const skills = program.command("skills").description("Manage skills");

skills
  .command("new <name>")
  .description("Scaffold a new local skill in ~/.openvox/skills/")
  .action(async (name: string) => {
    const home = process.env.HOME || process.env.USERPROFILE;
    if (!home) throw new Error("cannot determine home directory");
    const dir = path.join(home, ".openvox", "skills");
    await fs.mkdir(dir, { recursive: true });
    const file = path.join(dir, `${name}.py`);
    const stub = `from openvox.skills import BaseSkill, SkillContext


class ${toCamel(name)}(BaseSkill):
    id = "${name}"
    display_name = "${name.replace(/_/g, " ")}"
    description = "TODO: describe what this skill does."
    parameters = {
        "type": "object",
        "properties": {
            "input": {"type": "string"},
        },
        "required": ["input"],
    }

    async def run(self, args, ctx: SkillContext):
        return {"echo": args.get("input", "")}
`;
    await fs.writeFile(file, stub);
    console.log(green(`✓ wrote ${file}`));
    console.log(dim("Restart the core service to pick up the new skill."));
  });

// ── skills list ────────────────────────────────────────────────────
skills
  .command("list")
  .description("List all installed skills")
  .action(async () => {
    const list = await api<any[]>("/api/v1/skills");
    for (const s of list) {
      console.log(`${green(s.id.padEnd(22))} ${s.display_name}`);
      console.log(`  ${dim(s.description)}`);
    }
  });

function toCamel(s: string): string {
  return s
    .split(/[_-]/)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join("");
}

program.parse(process.argv);
