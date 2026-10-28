const vscode = require("vscode");
const cp = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

let provider;
let captureTimer;
let idleTimer;
let diagTimer;
let lastDiagSig = "";
let execSeq = 0;
const pendingSaves = new Set();

const cfg = () => vscode.workspace.getConfiguration("rewind");
const scriptPath = () => cfg().get("scriptPath") || path.join(__dirname, "rewind.py");
const openPanel = () => vscode.commands.executeCommand("rewind.panel.focus");

function root() {
  const f = vscode.workspace.workspaceFolders;
  return f && f[0].uri.fsPath;
}

function run(args, cwd = root(), input) {
  return new Promise((resolve) => {
    if (!cwd) return resolve({ code: 1, text: "Open a folder to use Rewind." });
    const child = cp.execFile(
      cfg().get("pythonPath") || "python",
      [scriptPath(), "--project", cwd, ...args],
      { cwd, maxBuffer: 8 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout, stderr) =>
        resolve({ code: err ? (typeof err.code === "number" ? err.code : 1) : 0, text: (stdout || "") + (stderr || "") })
    );
    if (input !== undefined) child.stdin.end(input);
  });
}

async function runNotify(args, okMsg) {
  const r = await run(args);
  if (r.code === 0) vscode.window.showInformationMessage(okMsg || r.text.trim().split("\n")[0] || "Done");
  else vscode.window.showWarningMessage(r.text.trim().slice(0, 300) || "Rewind command failed");
  provider.refresh();
  return r;
}

const ask = (prompt, placeHolder, value) =>
  vscode.window.showInputBox({ prompt, placeHolder, value, ignoreFocusOut: true });

// Rewind infers the four answers from recent activity; Enter accepts, or edit any of them.
async function pause() {
  let guess = {};
  try {
    guess = JSON.parse((await run(["suggest"])).text.trim() || "{}");
  } catch (_) {}
  const fields = [
    ["hypothesis", "Current hypothesis (Enter = accept suggestion)"],
    ["testing", "What are you testing right now?"],
    ["result", "Last result"],
    ["next", "Next thing you intended to try"],
  ];
  const args = ["pause"];
  for (const [k, prompt] of fields) {
    const v = await ask(prompt, undefined, guess[k]);
    if (v === undefined) return;
    if (v.trim()) args.push("--" + k, v);
  }
  await runNotify(args, "Rewind saved your state. Safe to leave.");
}

const autoPauseEnabled = () => cfg().get("autoPause");

function activeRelFile() {
  const ed = vscode.window.activeTextEditor;
  const r = root();
  return ed && r ? path.relative(r, ed.document.uri.fsPath).replace(/\\/g, "/") : undefined;
}

async function showOutput(args) {
  const r = await run(args);
  const doc = await vscode.workspace.openTextDocument({ content: r.text || "(no output)", language: "markdown" });
  vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.Beside });
  return r;
}

const commands = {
  "rewind.pause": pause,
  "rewind.refresh": () => provider.refresh(true),
  "rewind.openPanel": () => openPanel(),
  "rewind.task": async () => {
    const t = await ask("What are you working on?");
    if (t) await runNotify(["task", t], "Task set");
  },
  "rewind.done": () => runNotify(["task", "--done"]),
  "rewind.checkpoint": async () => {
    const t = await ask("Checkpoint title");
    if (t) await runNotify(["checkpoint", t], "Checkpoint saved");
  },
  "rewind.decide": async () => {
    const t = await ask("Decision");
    if (!t) return;
    const why = await ask("Why?");
    const f = activeRelFile();
    await runNotify(["decide", t, ...(why ? ["--why", why] : []), ...(f ? ["--file", f] : [])], "Decision recorded");
  },
  "rewind.fail": async () => {
    const t = await ask("What did you try that didn't work?");
    if (!t) return;
    const why = await ask("Why did it fail?");
    const f = activeRelFile();
    await runNotify(["fail", t, ...(why ? ["--why", why] : []), ...(f ? ["--file", f] : [])], "Dead end recorded");
  },
  "rewind.check": async () => {
    const t = await ask("Approach you are about to try");
    if (!t) return;
    const r = await run(["check", t]);
    (r.code === 3 ? vscode.window.showWarningMessage : vscode.window.showInformationMessage)(r.text.trim());
  },
  "rewind.ask": async () => {
    const q = await ask("Ask Rewind", "Where did I stop? / What fixes have I tried?");
    if (q) await showOutput(["ask", q]);
  },
  "rewind.showTried": () => showOutput(["ask", "what have I tried"]),
  "rewind.handoff": async () => {
    const r = await run(["handoff"]);
    await vscode.env.clipboard.writeText(r.text);
    vscode.window.showInformationMessage("Copied. Paste into any AI chat to continue from here.");
  },
  "rewind.handoffAt": async (id) => {
    const r = await run(["handoff", ...(id ? ["--at", String(id)] : [])]);
    await vscode.env.clipboard.writeText(r.text);
    vscode.window.showInformationMessage(`Handoff from milestone #${id} copied.`);
  },
  "rewind.confirm": async (id) => {
    await run(["confirm", String(id)]);
    provider.refresh(true);
  },
  "rewind.dismiss": async (id) => {
    await run(["dismiss", String(id)]);
    provider.refresh(true);
  },
  "rewind.dismissAway": async () => {
    await run(["dismiss-away"]);
    provider.refresh(true);
  },
  "rewind.graph": async () => {
    const file = path.join(os.tmpdir(), `rewind-graph-${Date.now()}.html`);
    const r = await run(["graph", "--format", "html", "-o", file]);
    if (r.code !== 0) return vscode.window.showWarningMessage(r.text.trim().slice(0, 300) || "Graph failed");
    const panel = vscode.window.createWebviewPanel("rewindGraph", "Rewind: Context Graph", vscode.ViewColumn.Active, {
      enableScripts: true,
      retainContextWhenHidden: true,
    });
    panel.webview.html = fs.readFileSync(file, "utf8");
    panel.webview.onDidReceiveMessage(async (m) => {
      if (m.copy) {
        await vscode.env.clipboard.writeText(m.copy);
        vscode.window.showInformationMessage("Handoff copied. Paste it into any AI chat.");
      }
    });
  },
  "rewind.simplify": async () => {
    const f = activeRelFile();
    if (!f) return vscode.window.showInformationMessage("Open a file first.");
    await showOutput(["simplify", f]);
  },
  "rewind.mcpConfig": async () => {
    const snippet = JSON.stringify(
      { servers: { rewind: { type: "stdio", command: cfg().get("pythonPath") || "python", args: [scriptPath(), "--project", root() || ".", "mcp"] } } },
      null,
      2
    );
    await vscode.env.clipboard.writeText(snippet);
    vscode.window.showInformationMessage("MCP config copied. Paste it into .vscode/mcp.json or your AI tool's MCP settings.");
  },
  "rewind.why": async () => {
    const f = activeRelFile();
    if (!f) return vscode.window.showInformationMessage("Open a file first.");
    await showOutput(["why", f]);
  },
  "rewind.resume": async () => {
    const r = await run(["checkpoints", "-n", "30"]);
    const items = r.text.split("\n").filter(Boolean).map((l) => ({ label: l, id: (l.match(/^#(\d+)/) || [])[1] }));
    const pick = await vscode.window.showQuickPick(items.filter((i) => i.id), { placeHolder: "Resume from which milestone?" });
    if (!pick) return;
    const plan = await run(["resume", pick.id]);
    const ok = await vscode.window.showWarningMessage(plan.text.trim() + "\n\nApplying stashes uncommitted work first.", { modal: true }, "Apply");
    if (ok === "Apply") await runNotify(["resume", pick.id, "--apply"], "Restored milestone");
  },
};

class PanelProvider {
  constructor(ctx) {
    this.ctx = ctx;
    this.timer = undefined;
    this.state = undefined;
  }
  resolveWebviewView(view) {
    this.view = view;
    view.webview.options = { enableScripts: true };
    const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
    view.webview.html = fs.readFileSync(path.join(__dirname, "panel.html"), "utf8").replace(/__NONCE__/g, nonce);
    view.webview.onDidReceiveMessage((m) => {
      if (m.cmd === "ready") return this.refresh(true);
      if (m.cmd && commands[m.cmd]) commands[m.cmd](m.arg);
    });
    view.onDidChangeVisibility(() => view.visible && this.refresh(true));
  }
  refresh(now) {
    clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      const r = await run(["state"]);
      let state = null;
      try {
        state = JSON.parse(r.text.trim().split("\n").pop());
      } catch (_) {}
      this.state = state;
      if (this.view) this.view.webview.postMessage({ state });
    }, now ? 0 : 400);
  }
}

// editor problems (Dart analyzer, TypeScript, ...): new errors are remembered, cleared ones count as fixes
function collectDiagnostics() {
  const r = root();
  if (!r) return {};
  const out = {};
  for (const [uri, diags] of vscode.languages.getDiagnostics()) {
    if (uri.scheme !== "file" || !uri.fsPath.startsWith(r)) continue;
    const errs = diags.filter((d) => d.severity === vscode.DiagnosticSeverity.Error);
    if (!errs.length) continue;
    out[path.relative(r, uri.fsPath).replace(/\\/g, "/")] = errs.slice(0, 5).map((d) => d.message.split("\n")[0]);
    if (Object.keys(out).length >= 40) break;
  }
  return out;
}

function activate(ctx) {
  provider = new PanelProvider(ctx);
  ctx.subscriptions.push(
    vscode.window.registerWebviewViewProvider("rewind.panel", provider, { webviewOptions: { retainContextWhenHidden: true } })
  );
  for (const [id, fn] of Object.entries(commands)) ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));

  const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 0);
  bar.text = "$(history) Rewind";
  bar.tooltip = "Open Rewind";
  bar.command = "rewind.openPanel";
  bar.show();
  const pauseBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, -1);
  pauseBar.text = "$(debug-pause) Pause";
  pauseBar.tooltip = "Save where you are before you leave";
  pauseBar.command = "rewind.pause";
  pauseBar.show();
  ctx.subscriptions.push(bar, pauseBar);

  // saves: debounced, event-driven capture of just the saved files
  ctx.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      pendingSaves.add(doc.uri.fsPath);
      if (!cfg().get("captureOnSave")) return;
      clearTimeout(captureTimer);
      captureTimer = setTimeout(() => {
        const files = [...pendingSaves];
        pendingSaves.clear();
        run(["capture", "--file", ...files]).then(() => provider.refresh());
      }, 1500);
    })
  );

  // terminal commands and their errors (needs shell integration, on by default in recent VS Code)
  if (vscode.window.onDidStartTerminalShellExecution) {
    const buffers = new Map();
    const stripAnsi = (t) => t.replace(/\u001b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\u0007]*\u0007)/g, "");
    ctx.subscriptions.push(
      vscode.window.onDidStartTerminalShellExecution(async (e) => {
        const buf = { text: "", id: `${process.pid}-${Date.now()}-${++execSeq}` };
        buffers.set(e.execution, buf);
        try {
          for await (const chunk of e.execution.read()) buf.text = (buf.text + chunk).slice(-30000);
        } catch (_) {}
      }),
      vscode.window.onDidEndTerminalShellExecution(async (e) => {
        const buf = buffers.get(e.execution);
        buffers.delete(e.execution);
        const cmd = e.execution.commandLine && e.execution.commandLine.value;
        if (!cmd || !root() || !cfg().get("captureTerminal")) return;
        const code = typeof e.exitCode === "number" ? e.exitCode : 0;
        const execId = buf ? buf.id : `end-${process.pid}-${Date.now()}-${++execSeq}`;  // a repeated id is ignored by the engine
        const r = await run(["record", "--command", cmd, "--exit", String(code), "--id", execId], root(), stripAnsi(buf ? buf.text : ""));
        const notes = r.text.split("\n").filter((l) => l.startsWith("[rewind]")).map((l) => l.replace("[rewind] ", ""));
        if (notes.length) vscode.window.showWarningMessage(notes.slice(0, 3).join("\n").slice(0, 400), "Open Rewind").then((c) => c && openPanel());
        provider.refresh();
      })
    );
  }

  // problems reported by the editor itself, after a quiet period so half-typed code is ignored
  ctx.subscriptions.push(
    vscode.languages.onDidChangeDiagnostics(() => {
      if (!cfg().get("captureProblems")) return;
      clearTimeout(diagTimer);
      diagTimer = setTimeout(async () => {
        const data = JSON.stringify(collectDiagnostics());
        if (data === lastDiagSig) return;
        lastDiagSig = data;
        await run(["diag"], root(), data);
        provider.refresh();
      }, 8000);
    })
  );

  // coming back to the window: pick up branch switches/commits; leaving: schedule an inferred pause
  ctx.subscriptions.push(
    vscode.window.onDidChangeWindowState((st) => {
      clearTimeout(idleTimer);
      if (st.focused) {
        run(["capture"]).then(() => provider.refresh());
      } else if (autoPauseEnabled()) {
        idleTimer = setTimeout(() => run(["autopause"]).then(() => provider.refresh()), 10 * 60 * 1000);
      }
    })
  );

  // returning to a project: welcome back
  run(["state"]).then((r) => {
    let s = null;
    try {
      s = JSON.parse(r.text.trim().split("\n").pop());
    } catch (_) {}
    if (s && (s.away || s.stopped)) {
      const msg = s.away ? `Welcome back. ${s.away.commits.length} new commit(s) while you were away.` : "Welcome back. Rewind remembers where you stopped.";
      vscode.window.showInformationMessage(msg, "Open Rewind").then((c) => c && openPanel());
    }
    provider.refresh(true);
  });
}

function deactivate() {
  clearTimeout(captureTimer);
  clearTimeout(idleTimer);
  clearTimeout(diagTimer);
  if (!autoPauseEnabled() || !root()) return;
  try {
    cp.spawnSync(cfg().get("pythonPath") || "python", [scriptPath(), "--project", root(), "autopause"], { timeout: 4000 });
  } catch (_) {}
}

module.exports = { activate, deactivate };
