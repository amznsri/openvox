// One-shot smoke check for the action-command parser.
// Run from apps/dashboard:  npx tsx scripts/smoke-command-actions.mjs
//
// Not a proper test runner — the dashboard doesn't have one (yet).
// Stays here as a tiny manual gate so the parser doesn't silently
// break on a refactor. Add Vitest later if/when test count grows.
import {
  parseActionCommand,
  stripNavSuffix,
  HELP_SECTIONS,
} from "../src/lib/command-actions.ts";

const cases = [
  // Input, expected kind ("" = no match), expected arg
  ["test acme support",                "test_agent",            "acme support"],
  ["TEST ACME",                        "test_agent",            "ACME"],
  ["try   email assistant ",           "test_agent",            "email assistant"],
  ["play with Mira",                   "test_agent",            "Mira"],
  ["playground my agent",              "test_agent",            "my agent"],
  ["test",                             "",                      ""],
  ["test ",                            "",                      ""],
  ["create from template Email Assistant", "create_from_template", "Email Assistant"],
  ["new from template foo",            "create_from_template",  "foo"],
  ["use template Bar",                 "create_from_template",  "Bar"],
  ["instantiate baz",                  "create_from_template",  "baz"],
  ["disconnect alice@example.com",     "disconnect",            "alice@example.com"],
  ["connect gmail",                    "connect_gmail",         ""],
  ["connect google",                   "connect_gmail",         ""],
  ["connect calendar",                 "connect_gmail",         ""],
  ["Connect Gmail",                    "connect_gmail",         ""],
  ["connect gmail.",                   "connect_gmail",         ""],
  ["connect gmail please",             "",                      ""],
  ["help",                             "help",                  ""],
  ["?",                                "help",                  ""],
  ["what can i do",                    "help",                  ""],
  ["what can you do",                  "help",                  ""],
  ["commands",                         "help",                  ""],
  ["",                                 "",                      ""],
  ["random query",                     "",                      ""],
  ["agents",                           "",                      ""],

  // open_page verb — multiple phrasings + every sidebar destination.
  // Suffix-strip is exercised separately below.
  ["open overview",                    "open_page",             "overview"],
  ["open playground",                  "open_page",             "playground"],
  ["open agents",                      "open_page",             "agents"],
  ["Open Agents page",                 "open_page",             "Agents page"],
  ["open templates",                   "open_page",             "templates"],
  ["open skills",                      "open_page",             "skills"],
  ["open schedules",                   "open_page",             "schedules"],
  ["open evals",                       "open_page",             "evals"],
  ["go to evals",                      "open_page",             "evals"],
  ["go to evals page",                 "open_page",             "evals page"],
  ["open providers",                   "open_page",             "providers"],
  ["open integrations",                "open_page",             "integrations"],
  ["open observability",               "open_page",             "observability"],
  ["open settings",                    "open_page",             "settings"],
  ["navigate to settings",             "open_page",             "settings"],
  ["show me playground",               "open_page",             "playground"],
  ["show integrations",                "open_page",             "integrations"],
  ["take me to providers",             "open_page",             "providers"],
  ["open",                             "",                      ""],          // empty arg
  ["go to ",                           "",                      ""],
];

// Suffix-strip cases (separate because they exercise a different
// helper from the parser).
const stripCases = [
  ["agents page",                "agents"],
  ["evals tab",                  "evals"],
  ["settings section",           "settings"],
  ["agents page tab",            "agents"],          // repeated strip
  ["AGENTS PAGE",                "AGENTS"],          // case preserved on the kept part
  ["playground",                 "playground"],      // no suffix → unchanged
  ["",                           ""],
  ["page",                       ""],                 // bare suffix → empty
];

let pass = 0, fail = 0;
for (const [input, expKind, expArg] of cases) {
  const r = parseActionCommand(input);
  const gotKind = r?.kind || "";
  const gotArg = r?.arg || "";
  const ok = gotKind === expKind && gotArg === expArg;
  if (ok) pass++;
  else {
    fail++;
    console.log(`x ${JSON.stringify(input)} -> expected (${expKind}, ${JSON.stringify(expArg)}), got (${gotKind}, ${JSON.stringify(gotArg)})`);
  }
}
console.log(`\nparser: ${pass}/${pass+fail} pass`);

let sPass = 0, sFail = 0;
for (const [input, expected] of stripCases) {
  const got = stripNavSuffix(input);
  if (got === expected) {
    sPass++;
  } else {
    sFail++;
    console.log(`x stripNavSuffix ${JSON.stringify(input)} -> expected ${JSON.stringify(expected)}, got ${JSON.stringify(got)}`);
  }
}
console.log(`stripNavSuffix: ${sPass}/${sPass+sFail} pass`);

console.log(`help sections: ${HELP_SECTIONS.length} (expect 4)`);
process.exit(fail + sFail > 0 ? 1 : 0);
