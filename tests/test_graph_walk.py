import json
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import Base

HARNESS = r"""
const fs=require("fs"),vm=require("vm");
const html=fs.readFileSync(process.argv[2],"utf8");
const js=html.split("<script>")[1].split("</script>")[0];
const stub=new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?()=>0:(k==="getBoundingClientRect"?()=>({width:900,height:600,left:0,top:0}):stub),apply:()=>stub,set:()=>true});
const ctx=vm.createContext({document:stub,location:{hash:""},addEventListener(){},requestAnimationFrame(){return 0},cancelAnimationFrame(){},navigator:{},console});
vm.runInContext(js+";globalThis.__t={chain,byId,D,reasoningPath,chainHtml,select};",ctx);
const t=ctx.__t,find=(ty,s)=>t.D.nodes.find(n=>n.type===ty&&n.label.includes(s));
const dec=find("decision","retry"),err=find("error","timed out"),att=find("failed","increase timeout");
const fmt=(id,dir)=>t.chain(id,dir,5).map(s=>s.via+"|"+t.byId[s.id].type+"|"+t.byId[s.id].label);
t.select(dec.id);
console.log(JSON.stringify({
  decWhy:fmt(dec.id,"why"), attWhy:fmt(att.id,"why"), errThen:fmt(err.id,"then"), decThen:fmt(dec.id,"then"),
  pathNodes:[...t.reasoningPath(dec.id).nodes].length, pathEdges:[...t.reasoningPath(dec.id).edges].length,
  drawer:t.chainHtml(dec.id)
}));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class GraphWalkTests(Base):
    def scenario(self):
        self.rw("task", "fix timeouts")
        self.record("flutter run", 1, "Error: Connection timed out after 30s\n")
        self.rw("fail", "increase timeout to 60 seconds", "--why", "still hangs")
        self.rw("decide", "use retry with exponential backoff", "--why", "raising the timeout did not help")
        Path(self.repo, "app.py").write_text("print('retry with backoff')\n")
        self.rw("capture")
        self.record("flutter run", 0, "")

    def walk(self):
        self.scenario()
        html = Path(self.tmp.name) / "g.html"
        self.rw("graph", "-o", str(html))
        script = Path(self.tmp.name) / "harness.js"
        script.write_text(HARNESS, encoding="utf-8")
        r = subprocess.run(["node", str(script), str(html)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_decision_walks_back_to_its_cause_step_by_step(self):
        w = self.walk()
        why = w["decWhy"]
        self.assertTrue(why[0].startswith("because of|failed|increase timeout"), why)       # decision <- dead end
        self.assertTrue(any(s.startswith("was an attempt to fix|error|Error: Connection timed out") for s in why), why)  # <- error

    def test_an_error_shows_what_was_tried_and_what_came_of_it(self):
        w = self.walk()
        then = w["errThen"]
        self.assertTrue(any(s.startswith("tried to fix it:|failed|") for s in then), then)
        self.assertTrue(any(s.startswith("led to decision|decision|use retry") for s in then), then)
        self.assertTrue(any(s.startswith("fixed by|checkpoint|") and "Fixed" in s for s in then), then)

    def test_a_dead_end_knows_the_error_it_was_for(self):
        w = self.walk()
        self.assertTrue(w["attWhy"][0].startswith("was an attempt to fix|error|"), w["attWhy"])

    def test_selecting_highlights_a_connected_path_and_the_drawer_lists_the_walk(self):
        w = self.walk()
        self.assertGreaterEqual(w["pathNodes"], 3)
        self.assertGreaterEqual(w["pathEdges"], 2)
        self.assertIn("Why this exists", w["drawer"])
        self.assertIn("What came of it", w["drawer"])
        self.assertIn('data-id="', w["drawer"])  # every step is clickable

    def test_nothing_recorded_means_an_honest_empty_state(self):
        self.rw("decide", "a decision with no known cause", "--why", "just because I like it")
        html = Path(self.tmp.name) / "g.html"
        self.rw("graph", "-o", str(html))
        script = Path(self.tmp.name) / "h2.js"
        script.write_text(HARNESS.split("const t=ctx.__t")[0] + r"""
const t=ctx.__t,d=t.D.nodes.find(n=>n.type==="decision");
console.log(JSON.stringify({drawer:t.chainHtml(d.id),why:t.chain(d.id,"why",5).length}));""", encoding="utf-8")
        r = subprocess.run(["node", str(script), str(html)], capture_output=True, text=True)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["why"], 0)
        self.assertIn("No recorded cause", out["drawer"])
