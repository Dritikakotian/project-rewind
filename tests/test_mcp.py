import json

from helpers import Base


class McpTests(Base):
    def talk(self, *calls):
        reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        for i, (method, params) in enumerate(calls, start=2):
            reqs.append({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        r = self.run_cli("mcp", stdin="\n".join(json.dumps(x) for x in reqs) + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        return [json.loads(l) for l in r.stdout.splitlines()]

    def call(self, name, **args):
        return ("tools/call", {"name": name, "arguments": args})

    def text(self, reply):
        return reply["result"]["content"][0]["text"]

    def test_server_tells_agents_to_use_rewind_first(self):
        init = self.talk()[0]["result"]
        self.assertIn("rewind_context", init["instructions"])
        self.assertIn("rewind_check_approach", init["instructions"])

    def test_tools_are_listed_with_directive_descriptions(self):
        tools = {t["name"]: t for t in self.talk(("tools/list", {}))[1]["result"]["tools"]}
        for name in ("rewind_context", "rewind_check_approach", "rewind_record_failed", "rewind_record_decision",
                     "rewind_trace", "rewind_ask"):
            self.assertIn(name, tools)
        self.assertIn("FIRST", tools["rewind_context"]["description"])
        self.assertIn("BEFORE", tools["rewind_check_approach"]["description"])

    def test_an_agent_can_record_a_dead_end_and_be_warned_later(self):
        replies = self.talk(
            self.call("rewind_record_failed", approach="increase timeout to 60 seconds", why="still hangs"),
            self.call("rewind_check_approach", approach="raise the timeout to 60"),
            self.call("rewind_record_decision", decision="use retry with backoff", why="timeout bump failed"),
            self.call("rewind_trace", query="retry with backoff"),
            self.call("rewind_context"),
        )
        self.assertIn("already attempted", self.text(replies[2]))
        self.assertIn("still hangs", self.text(replies[4]))      # trace: decision <-because- dead end
        self.assertIn("retry with backoff", self.text(replies[5]))

    def test_unknown_tool_and_method_are_errors_not_crashes(self):
        replies = self.talk(("tools/call", {"name": "nope", "arguments": {}}), ("bogus/method", {}))
        self.assertIn("error", replies[1])
        self.assertIn("error", replies[2])

    def test_garbage_input_does_not_kill_the_server(self):
        r = self.run_cli("mcp", stdin='not json\n{"jsonrpc":"2.0","id":5,"method":"ping"}\n')
        self.assertEqual(json.loads(r.stdout.splitlines()[0])["id"], 5)
