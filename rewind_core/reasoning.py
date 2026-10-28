"""Reasoning links in the context graph: why a decision exists, what resolved an error, what was tried."""
import os

from .util import *  # noqa: F401,F403
from .project import *  # noqa: F401,F403
from .semantic import *  # noqa: F401,F403

REASON_RELS = ("because", "attempt-for", "resolved-by", "decided")
MEMORY_KINDS = ("decision", "failed", "error", "pause", "task")


def checkpoint_node(p, cid):
    r = p.db.execute("SELECT id FROM nodes WHERE type='checkpoint' AND key=?", (str(cid),)).fetchone()
    return r["id"] if r else None


def resolve_error(p, error_id, cid):
    """error --resolved-by--> the fix milestone"""
    n = checkpoint_node(p, cid)
    if n:
        p.link(error_id, n, "resolved-by")


def link_reasoning(p, nid, text):
    """decision --because--> the dead ends / errors it responds to (semantic match, top 3)."""
    me = p.db.execute("SELECT data FROM nodes WHERE id=?", (nid,)).fetchone()
    why = (json.loads(me["data"] or "{}").get("why") or "") if me else ""
    probes = [t for t in (text, why) if t.strip()]  # the reason alone is often the sharper signal
    rows = p.db.execute("SELECT * FROM nodes WHERE type IN ('failed','error') AND id!=? ORDER BY id DESC LIMIT 200",
                        (nid,)).fetchall()
    scored = []
    for r in rows:
        cand = r["label"] + " " + (p.nd(r).get("why") or "")
        scored.append((max(similarity(pr, cand) for pr in probes), r["id"])) if probes else None
    for score, rid in sorted(scored, reverse=True)[:3]:
        if score >= 0.2:
            p.link(nid, rid, "because")


def _line(p, r):
    d = p.nd(r)
    extra = f" -- {d['why']}" if d.get("why") else (f" => {d['solution']}" if d.get("solution") else "")
    return f"[{r['type']}] {r['label'][:100]}{extra[:120]}"


def trace(p, query, depth=3):
    """Walk the reasoning chain around a file, error or topic: what led here, what was tried, what fixed it."""
    q = query.replace("\\", "/").strip()
    f = p.db.execute("SELECT * FROM nodes WHERE type='file' AND (key=? OR key LIKE ?)", (q, "%/" + os.path.basename(q))).fetchone()
    if f:
        starts = p.db.execute(
            "SELECT n.* FROM nodes n JOIN edges e ON e.src=n.id WHERE e.dst=? AND n.type IN ('decision','failed','error','pause') "
            "ORDER BY n.id DESC LIMIT 6", (f["id"],)).fetchall()
    else:
        rows = p.db.execute("SELECT * FROM nodes WHERE type IN ('decision','failed','error','task') ORDER BY id DESC LIMIT 300").fetchall()
        starts = [r for _, r in best_matches(q, rows, lambda r: r["label"] + " " + (p.nd(r).get("why") or ""), 3, 0.15)]
    if not starts:
        return f'Nothing recorded about "{query}".'
    lines, seen = [f'Trace for "{query}":'], set()

    def walk(r, level, via):
        if r["id"] in seen:
            return
        seen.add(r["id"])
        lines.append("  " * level + (via + " " if via else "") + _line(p, r))
        if level >= depth:
            return
        marks = ",".join("?" * len(REASON_RELS))
        for e in p.db.execute(f"SELECT * FROM edges WHERE (src=? OR dst=?) AND rel IN ({marks})", (r["id"], r["id"], *REASON_RELS)):
            out_edge = e["src"] == r["id"]
            other = p.db.execute("SELECT * FROM nodes WHERE id=?", (e["dst"] if out_edge else e["src"],)).fetchone()
            if other and other["type"] != "file":
                walk(other, level + 1, f"-{e['rel']}->" if out_edge else f"<-{e['rel']}-")

    for r in starts:
        walk(r, 0, "")
    return "\n".join(lines)
