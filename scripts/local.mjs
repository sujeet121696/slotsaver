// One command for local work: npm run local  (or one step: test | clean | build | run)
//
//   test   both test suites (Python unittest + node --test), no keys, no calls
//   clean  remove generated files: board.json, site/, .serve/, __pycache__/
//   build  regenerate site/ (the deploy folder), bundle-check the Worker with
//          `wrangler deploy --dry-run`, and offer `wrangler login` if needed
//   run    serve the live board on 127.0.0.1 and play the MOCK evening run
//   tunnel everything above, plus a public Cloudflare quick-tunnel link
//
// The board server only ever serves .serve/ (the board, favicon and
// board.json), never the repo root with .env and private notes.
// Mock only: `--real` is refused here, real calls stay a deliberate manual
// command (docs/RULES.md). Extra args go to run_demo, e.g.
//   npm run local -- --brain strands

import { spawn, spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline/promises";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
// Not 8787: that is wrangler dev's default, so other projects often hold it.
const PORT = process.env.PORT || "8790";
const IS_WIN = process.platform === "win32";

// wrangler's own entry point, run with this Node: no npx, no shell, so
// nothing is re-parsed by cmd.exe on Windows.
const WRANGLER = join(ROOT, "node_modules", "wrangler", "bin", "wrangler.js");

function wrangler(args, options) {
  if (!existsSync(WRANGLER)) {
    console.error("✖ wrangler isn't installed: run `npm install` first");
    process.exit(1);
  }
  return [process.execPath, [WRANGLER, ...args], options];
}

const PYTHON = [".venv/Scripts/python.exe", ".venv/bin/python"]
  .map((p) => join(ROOT, p))
  .find(existsSync) ?? (IS_WIN ? "python" : "python3");

function step(title) {
  console.log(`\n── ${title} ${"─".repeat(Math.max(0, 60 - title.length))}`);
}

/** Runs a command with live output; exits the script if it fails. */
function run(cmd, args) {
  const res = spawnSync(cmd, args, { cwd: ROOT, stdio: "inherit" });
  if (res.status !== 0) {
    console.error(`\n✖ failed: ${cmd} ${args.join(" ")}`);
    process.exit(res.status || 1);
  }
}

function test() {
  step("test: Python");
  run(PYTHON, ["-m", "unittest", "discover", "tests"]);
  step("test: Worker loop");
  run(process.execPath, ["--test", "tests/worker_evening.test.mjs"]);
}

function clean() {
  step("clean");
  const removed = [];
  for (const p of ["board.json", "board.json.tmp", "site", ".serve"]) {
    if (existsSync(join(ROOT, p))) {
      rmSync(join(ROOT, p), { recursive: true, force: true });
      removed.push(p);
    }
  }
  for (const dir of ["slotsaver", "tests"]) {
    const cache = join(ROOT, dir, "__pycache__");
    if (existsSync(cache)) {
      rmSync(cache, { recursive: true, force: true });
      removed.push(`${dir}/__pycache__`);
    }
  }
  console.log(removed.length ? `removed: ${removed.join(", ")}` : "nothing to remove");
}

const INDEX_HTML = `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<meta http-equiv="refresh" content="0; url=demo_board.html?demo=1">
<link rel="canonical" href="demo_board.html?demo=1"><title>SlotSaver</title>
</head><body><p>Redirecting to the
<a href="demo_board.html?demo=1">live demo board</a>…</p></body></html>
`;

async function build() {
  step("build: site/");
  const site = join(ROOT, "site");
  mkdirSync(site, { recursive: true });
  copyFileSync(join(ROOT, "demo_board.html"), join(site, "demo_board.html"));
  copyFileSync(join(ROOT, "assets", "favicon.svg"), join(site, "favicon.svg"));
  writeFileSync(join(site, "index.html"), INDEX_HTML);
  console.log(`site/: ${readdirSync(site).join(", ")}`);

  step("build: Worker bundle (dry run, nothing deployed)");
  run(...wrangler(["deploy", "--dry-run", "--outdir", ".wrangler/dry-run"]));

  step("build: Cloudflare login");
  await ensureWranglerLogin();
}

/** Only deploying needs a login; the local run doesn't. Logged in, `wrangler
 * whoami` prints a "Token Permissions" table, so its absence means no login
 * (or no network). */
async function ensureWranglerLogin() {
  const [cmd, args] = wrangler(["whoami"]);
  const res = spawnSync(cmd, args, { cwd: ROOT, encoding: "utf8" });
  if ((res.stdout || "").includes("Token Permissions")) {
    console.log("logged in to Cloudflare (needed only for deploys)");
    return;
  }
  console.log("not logged in to Cloudflare (or offline). Only deploying needs it.");
  if (!process.stdin.isTTY) {
    console.log("run `npx wrangler login` when you want to deploy");
    return;
  }
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const answer = (await rl.question("Log in now with `npx wrangler login`? (y/N) ")).trim().toLowerCase();
  rl.close();
  if (answer === "y" || answer === "yes") {
    run(...wrangler(["login"]));
  } else {
    console.log("skipped; run `npx wrangler login` before deploying");
  }
}

// What the board server may hand out: only these files, copied into .serve/.
// Never the repo root, which holds .env, .dev.vars and private notes; that
// matters most once a tunnel makes the server public.
const SERVE_DIR = join(ROOT, ".serve");
const SERVE_INDEX = `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta http-equiv="refresh" content="0; url=demo_board.html">
<title>SlotSaver</title></head><body><a href="demo_board.html">live board</a></body></html>
`;

function prepareServeDir() {
  rmSync(SERVE_DIR, { recursive: true, force: true });
  mkdirSync(SERVE_DIR, { recursive: true });
  copyFileSync(join(ROOT, "demo_board.html"), join(SERVE_DIR, "demo_board.html"));
  copyFileSync(join(ROOT, "assets", "favicon.svg"), join(SERVE_DIR, "favicon.svg"));
  writeFileSync(join(SERVE_DIR, "index.html"), SERVE_INDEX);
}

/** True when something already accepts connections on the port. Checked by
 * connecting, because Python's server can bind a busy port on Windows
 * without an error and then never receive the requests. */
function portInUse(port) {
  return new Promise((resolve) => {
    const socket = connect({ host: "127.0.0.1", port: Number(port) });
    socket.setTimeout(1000);
    socket.once("connect", () => { socket.destroy(); resolve(true); });
    socket.once("timeout", () => { socket.destroy(); resolve(false); });
    socket.once("error", () => resolve(false));
  });
}

async function serveAndRun(demoArgs, { tunnel = false } = {}) {
  if (demoArgs.includes("--real")) {
    console.error("✖ `npm run local` is mock only. Real calls are a manual command, see README section 6.");
    process.exit(1);
  }
  if (await portInUse(PORT)) {
    console.error(`✖ port ${PORT} is already in use (another dev server?). Pick another, e.g. PORT=8791 npm run local`);
    process.exit(1);
  }
  const cloudflared = tunnel ? findCloudflared() : null;

  prepareServeDir();
  step(`run: board on http://127.0.0.1:${PORT}/demo_board.html`);
  const children = [];
  const server = spawn(PYTHON, ["-m", "http.server", PORT, "--bind", "127.0.0.1", "--directory", SERVE_DIR], {
    cwd: ROOT,
    stdio: ["ignore", "ignore", "inherit"],
  });
  children.push(server);
  const stop = () => {
    for (const child of children) child.kill();
    process.exit(0);
  };
  process.on("SIGINT", stop);
  server.on("exit", (code) => {
    if (code) {
      console.error(`✖ board server exited (code ${code}). Is port ${PORT} in use? Try PORT=8788.`);
      stop();
    }
  });

  if (cloudflared) children.push(startTunnel(cloudflared));

  console.log(`open the board now; the mock run starts in ${tunnel ? 8 : 3}s and paces calls 2s apart`);
  setTimeout(() => {
    step("run: mock evening run (no calls, no credit)");
    const args = ["-m", "slotsaver.run_demo", "--slow", "2", ...demoArgs];
    // board.json goes into .serve/ next to the board, so only it is served
    const env = { ...process.env, BOARD_PATH: join(SERVE_DIR, "board.json") };
    const demo = spawn(PYTHON, args, { cwd: ROOT, stdio: "inherit", env });
    demo.on("exit", (code) => {
      if (code) console.error(`✖ run_demo exited with code ${code}`);
      console.log(`\nboard still live at http://127.0.0.1:${PORT}/demo_board.html (Ctrl+C to stop)`);
    });
  }, tunnel ? 8000 : 3000); // a quick tunnel needs a few seconds to come up
}

function findCloudflared() {
  const res = spawnSync("cloudflared", ["--version"], { encoding: "utf8" });
  if (res.status !== 0) {
    console.error(
      "✖ cloudflared isn't installed. Install it, then retry:\n" +
        "    winget install --id Cloudflare.cloudflared      (Windows)\n" +
        "    brew install cloudflared                       (macOS)"
    );
    process.exit(1);
  }
  return "cloudflared";
}

/** A Cloudflare quick tunnel: no account or login, a random
 * https://*.trycloudflare.com URL that lives until Ctrl+C.
 *
 * It runs with an empty --config: otherwise cloudflared also loads
 * ~/.cloudflared/config.yml, whose named-tunnel ingress rules override
 * --url and turn every request into a 404. */
function startTunnel(cloudflared) {
  step("tunnel: public link (anyone with it can view the board)");
  const config = join(tmpdir(), "slotsaver-quick-tunnel.yml");
  writeFileSync(config, "# empty on purpose: keep ~/.cloudflared/config.yml out of quick tunnels\n");
  const proc = spawn(
    cloudflared,
    ["tunnel", "--config", config, "--no-autoupdate", "--url", `http://127.0.0.1:${PORT}`],
    { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] }
  );
  let found = null;
  const watch = (chunk) => {
    const url = !found && String(chunk).match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/);
    if (url) {
      found = url[0];
      announceWhenReady(`${found}/demo_board.html`);
    }
  };
  proc.stdout.on("data", watch);
  proc.stderr.on("data", watch);
  proc.on("exit", (code) => {
    if (!found) console.error(`✖ cloudflared exited (code ${code}) before giving a URL`);
  });
  return proc;
}

/** cloudflared prints the URL a few seconds before its DNS name resolves,
 * so only show it once the board actually loads through it. */
async function announceWhenReady(url) {
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
      if (res.ok) {
        console.log(`\n🌐 public board: ${url}`);
        console.log("   it serves only the board files from .serve/ (fictional demo data)\n");
        return;
      }
    } catch { /* DNS not ready yet */ }
    await new Promise((r) => setTimeout(r, 2000));
  }
  console.error(`✖ the tunnel URL never answered: ${url}`);
}

const argv = process.argv.slice(2);
// `npm run local -- --brain strands`: flags with no task mean "all"
const [task, ...rest] = argv.length && !argv[0].startsWith("--") ? argv : ["all", ...argv];
const steps = { test, clean, build };

if (task === "all" || task === "tunnel") {
  test();
  clean();
  await build();
  await serveAndRun(rest, { tunnel: task === "tunnel" });
} else if (task === "run") {
  await serveAndRun(rest);
} else if (steps[task]) {
  await steps[task]();
} else {
  console.error(`unknown task "${task}": use test | clean | build | run | tunnel (or nothing for all)`);
  process.exit(1);
}
