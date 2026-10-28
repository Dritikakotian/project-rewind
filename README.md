# Project Rewind  (v0.7.1)

The persistent memory and reasoning layer for developers and AI coding agents.

Rewind records what you are doing, what failed, and why you decided things, so you (or an AI assistant) can pick up
exactly where work stopped, without re-explaining the project. It is local-first: Python 3 standard library + SQLite +
git. Nothing leaves your machine.

**Watch the 1-minute demo:** [docs/project-rewind-demo.mp4](https://github.com/Dritikakotian/project-rewind/blob/main/docs/project-rewind-demo.mp4)
(a real engine run rendered with the real panel and graph; it is not a screen recording of VS Code).

## Contents
1. [Quick start](#quick-start)  2. [What you see in VS Code](#what-you-see-in-vs-code)  3. [Command line](#command-line)
4. [Automatic capture](#automatic-capture)  5. [Trust and evidence](#trust-and-evidence)
6. [The context graph](#the-context-graph)  7. [For AI agents](#for-ai-agents)  8. [Privacy and safety](#privacy-and-safety)
9. [Settings](#settings)  10. [Troubleshooting](#troubleshooting)  11. [Development](#development)  12. [Known limits](#known-limits)

## Quick start
**Install from GitHub (no build tools needed):**
1. Download [`dist/project-rewind-0.7.1.vsix`](https://github.com/Dritikakotian/project-rewind/raw/main/dist/project-rewind-0.7.1.vsix).
2. Close all VS Code windows, then run `code --install-extension project-rewind-0.7.1.vsix --force`
   (or Extensions panel, `...` menu, *Install from VSIX*).
3. Open any project folder and click the clock icon in the left bar.

Needs VS Code 1.93+, Python 3 and git on PATH. Everything below is captured without typing anything.

**Build from source** (needs Node.js): `git clone https://github.com/Dritikakotian/project-rewind.git`, then
`python build.py --install`. **Command line only:** `python rewind.py` inside any project folder.

**Command line:** `python rewind.py` in a project shows where you stopped. Use `--project PATH` for another folder.

## What you see in VS Code
The Rewind panel (clock icon in the activity bar), top to bottom:

| Area | What it shows |
|---|---|
| **Copy for AI** | One click copies a short, secret-free handoff to paste into any AI chat. |
| **Pause / Resume / Graph** | Pause saves your mental state (answers are pre-filled from your activity). Resume previews, then restores a milestone. Graph opens the context graph. |
| **While you were away** | New commits, merges and dependency changes since you left (dismissable). |
| **Current mission** | The task, a status (*In progress* / *Blocked by N open errors*), the **Next** step, progress and active files. |
| **Open errors** | Unresolved errors with how often they were seen and the command that caused them. |
| **Where you stopped** | Hypothesis, what you were testing, the last result and the next step; flagged if related files have since changed. |
| **Tasks, Decisions, Dead ends** | Collapsible lists. Inferred items show a confidence level and their evidence. |
| **Timeline** | Milestones grouped by day; each has a *Copy* button that copies a handoff from that moment. |
| **Add manually** | Task, decision, dead end, ask: only needed to override the automatic capture. |

Also available from the Command Palette (`Ctrl+Shift+P`, type "Rewind"): Why is this file like this, Suggest
simplifications, Copy MCP config, Was this already tried, and more. The status bar has *Rewind* and *Pause*.

## Command line
```
python rewind.py                       where you stopped (+ what changed while you were away)
python rewind.py task "Fix login"      or let it infer the task from your branch / edits
python rewind.py pause                 save hypothesis / testing / result / next (blank = inferred)
python rewind.py fail "x" --why "y"    remember a dead end;   check "x"  warns if you are about to repeat it
python rewind.py decide "x" --why "y"  remember a decision
python rewind.py trace app.py          the chain of attempts, decisions and fixes behind a file / error / topic
python rewind.py handoff [--focus ".."] [--at N]   minimal, redacted context to paste into any AI chat
python rewind.py resume N [--apply]    preview, then restore a milestone (stashes your work first)
python rewind.py graph                 interactive context graph (HTML);  --format json|mermaid
python rewind.py ask "what have I tried?"          python rewind.py simplify FILE
python rewind.py confirm ID | dismiss ID           mark an inferred memory right / wrong
python rewind.py agent-setup [--write --target claude|copilot|agents|cursor]
python rewind.py gc                    clean up old data;   projects   lists every project with memory
python rewind.py state | suggest | autopause | diag | record --command C --exit N [--id X]   (used by the extension)
```
Data lives in `~/.rewind/projects/<name>-<hash>/` (override with `REWIND_HOME`). `CONTEXT.md` there is always the latest
ready-to-paste handoff.

## Automatic capture
In VS Code these are recorded without typing:
- **Edits** on save, **commits** and **branch switches** (also when you return to the window).
- **Terminal commands and their errors** (needs VS Code shell integration, on by default).
- **Problems-panel errors**, remembered after a quiet period and marked fixed when they clear.
- **TODO / FIXME** comments become tasks (done when the comment is removed); **NOTE / HACK / WHY** comments become decisions.
- **Commits** whose message explains a choice become decisions; reverts become dead ends.
- **Same error again after you edited files** becomes a failed attempt; **the command passing again** becomes a fix.
- **Pause**: leaving the window for 10 minutes, or closing VS Code, saves an inferred pause.

Inference is ordered by event sequence, not clock time, and repeated terminal reports are de-duplicated by execution id,
so results do not depend on machine speed.

## Trust and evidence
Everything Rewind infers carries a **confidence** (High / Medium / Low) and the **evidence** it is based on, for example
*"current branch is feature/login-timeout"* or *"api_client.py was edited after the error first appeared; the same error
occurred again (2x)"*. Expand "Inferred" under an item to see it. Press **Correct** (`rewind confirm ID`) to make it
certain, or **Wrong, hide it** (`rewind dismiss ID`): wrong inferences disappear and are never inferred again.

## The context graph
`Graph` opens an interactive graph (pan, zoom, drag nodes):
- **Milestones** run down the left along a dashed timeline; tasks, decisions, dead ends, errors, pauses and commits hang
  off the milestone where they happened. Files can be shown with the *Files* filter.
- **Walk the reasoning:** click any node. The drawer shows **Why this exists** (for a decision: the dead end it responds
  to, then the error that dead end was an attempt to fix, then the task) and **What came of it** (for an error: what was
  tried, the decision it led to, the fix). Every step is clickable, there is a Back button, and the chain is highlighted
  as an amber path in the graph.
- **Time machine:** the slider shows the project as it was at any earlier milestone.
- **Search and filters** by type; **Copy handoff** from any milestone.

## For AI agents
`python rewind.py mcp` is an MCP server with the tools `rewind_context`, `rewind_check_approach`,
`rewind_record_failed`, `rewind_record_decision`, `rewind_trace` and `rewind_ask`. Its instructions tell agents to read
context first, check an approach before trying it, and record dead ends. `agent-setup` writes the same rules into
`CLAUDE.md` / `AGENTS.md` / `copilot-instructions.md` (only with `--write`). In VS Code: *Rewind: Copy MCP Config for AI Tools*.

Matching ("already tried?", ask, handoff relevance) is built in and dependency-free: synonym groups, stemming, word
pairs, character trigrams and typo-tolerant fuzzy matching. Optional neural embeddings, still local:
`pip install sentence-transformers` and set `REWIND_EMBEDDINGS=st`.

## Privacy and safety
- `.env`, keys, `credentials*`, `secrets*` and anything in `.rewindignore` are never stored. Tokens, passwords, AWS /
  GitHub / OpenAI keys and JWTs are redacted from everything shown or handed off.
- The database is owner-only (POSIX), uses WAL with a busy timeout (parallel editor events), and a corrupt file is moved
  aside, never deleted.
- Optional encryption of saved patches and file snapshots: `pip install cryptography`, set `REWIND_ENCRYPT=1` (key in
  `~/.rewind/.key`). If requested but unavailable, Rewind refuses to store plaintext.
- Resume previews first (files recreated, side copies, conflicts), stashes your current work (recoverable), and never
  overwrites an untracked file: a differing one is saved as `file.rewind-cpN` instead.
- Weekly housekeeping prunes old events, heavy payloads of old milestones and orphan nodes; decisions, dead ends and
  milestones are kept.

## Settings
`rewind.pythonPath` (default `python`), `rewind.scriptPath` (default: bundled engine), `rewind.captureOnSave`,
`rewind.captureTerminal`, `rewind.captureProblems`, `rewind.autoPause`.

## Troubleshooting
- **Extension version missing or shows an old version.** VS Code keeps its own extension list in memory and can mark a
  CLI-installed version as removed when you reload. Close **all** VS Code windows, then run
  `python build.py --install` (or `code --install-extension vscode-extension/project-rewind-0.7.1.vsix --force`) from a
  normal terminal, and reopen VS Code. Do not Uninstall first.
- **Panel is empty or errors.** Check `python --version` works; if your command is `py` or `python3`, set `rewind.pythonPath`.
- **Terminal commands are not captured.** Search settings for `terminal.integrated.shellIntegration.enabled` and enable it,
  then open a new terminal. Commands run outside VS Code can use `python rewind.py run -- <command>`.
- **Non-git folder.** Edits, tasks, decisions and pauses work; branch, commit and "while you were away" need git.

## Development
```
rewind.py            entry point                       build.py       builds/installs/zips the extension
rewind_core/         the engine                        tests/         71 tests;  python run_tests.py [pattern]
demo/make_demo.py    regenerates docs/project-rewind-demo.mp4 (needs Edge and ffmpeg)
vscode-extension/    panel.html, extension.js          (rewind.py and rewind_core/ in it are generated; never edit them)
```
Layers (each imports only from those before it): `util` helpers, `storage` schema / WAL / recovery / gc / encryption,
`project` git and file capture, `semantic` local similarity, `reasoning` links and trace, `memory` inferred memories,
`handoff` relevance-ranked context, `graph` export and state, `simplify`, `commands`, `mcp_server`, `cli`.
`python build.py --zip` also writes `../project-rewind.zip` for sharing.

## License
MIT, see [LICENSE](LICENSE). Contributions and issues are welcome.

## Known limits
- Confidence scores are hand-set rules, not yet learned from your corrections.
- Memories recorded before v0.7 have no evidence or confidence.
- Matching is lexical and synonym-based by default, so very different wording of the same idea can be missed.
- Terminal capture needs VS Code shell integration; the simplifier is deepest for Python.
- Optional encryption and neural embeddings are untested on machines without those libraries installed.
