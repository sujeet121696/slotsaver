// PreToolUse guard for Bash/PowerShell: blocks the commands docs/RULES.md
// says only the owner runs. Exit 2 blocks the call and shows the reason.
// The read-only `wrangler deploy --dry-run` stays allowed.

const BLOCKED = [
  [/\bgit\s+(commit|push)\b/, "the owner commits and pushes manually"],
  [/\bgh\s+pr\s+create\b/, "the owner opens PRs manually"],
  [/\bwrangler\s+deploy\b(?![^\n]*--dry-run)/, "the owner deploys (rebuild site/ first)"],
  [/\bslotsaver\.test_call\b/, "real calls cost money and ring real phones"],
  [/\bslotsaver\.run_demo\b[^\n]*\s--real\b/, "real calls cost money and ring real phones"],
];

let raw = "";
for await (const chunk of process.stdin) raw += chunk;

let command = "";
try {
  command = JSON.parse(raw).tool_input?.command ?? "";
} catch {
  process.exit(0); // not a tool payload we understand: don't block
}

for (const [pattern, why] of BLOCKED) {
  if (pattern.test(command)) {
    console.error(`Blocked by .claude/hooks/guard.mjs: ${why}. Suggest the command to the user instead.`);
    process.exit(2);
  }
}
