"""Record LangChain and LangGraph tool calls as warrantgen trace lines.

Pass it as a callback: graph.invoke(state, {"callbacks": [Recorder("traces.jsonl")]}). It needs no warrant, so it can
run before one exists, next to Tenuo's own `auto_protect(mode="audit")`. Each line's step is the LangGraph node path
that made the call (a subgraph gives "team/researcher"), and its run is the top-level invocation.
"""
import json
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler


class Recorder(BaseCallbackHandler):
    def __init__(self, path, *, step=None):
        self.path = Path(path)
        self.step = step  # fixed step name, for agents that are not LangGraph graphs
        self.parents = {}
        self.tool_runs = set()

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        self.parents[run_id] = parent_run_id

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, metadata=None, inputs=None,
                      **kwargs):
        self.parents[run_id] = parent_run_id
        self.tool_runs.add(run_id)
        if parent_run_id in self.tool_runs:
            return  # the inner tool of a wrapper such as TenuoTool: already recorded once
        root = run_id
        while self.parents.get(root) is not None:
            root = self.parents[root]
        line = {"run": str(root), "step": self.step or node_path(metadata or {}),
                "tool": (serialized or {}).get("name") or kwargs.get("name"), "args": _args(inputs, input_str)}
        with self.path.open("a") as f:
            f.write(json.dumps(line, default=str) + "\n")


def node_path(metadata):
    """'team:<id>|researcher:<id>' -> 'team/researcher'; the graph's own node when not nested."""
    namespace = metadata.get("langgraph_checkpoint_ns") or metadata.get("langgraph_node") or "default"
    return "/".join(part.split(":")[0] for part in namespace.split("|"))


def _args(inputs, input_str):
    if isinstance(inputs, dict):
        return inputs
    try:
        parsed = json.loads(input_str)
        return parsed if isinstance(parsed, dict) else {"input": parsed}
    except (TypeError, ValueError):
        return {"input": input_str}
