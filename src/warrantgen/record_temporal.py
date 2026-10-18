"""Record Tenuo's Temporal decisions as warrantgen trace lines.

Pass it as the plugin's audit callback: TenuoPluginConfig(..., audit_callback=TemporalRecorder("traces.jsonl")).
Use dry_run=True with a broad warrant to observe a workflow before you have tight warrants. Each workflow execution
is one run; each step is the set of tools on the warrant that checked the call, so a workflow that mints one warrant
per phase gets one step per phase.
"""
import json
import threading
from pathlib import Path


class TemporalRecorder:
    def __init__(self, path, *, step=None):
        self.path = Path(path)
        self.step = step  # optional event -> step name, e.g. lambda e: e.activity_name
        self._lock = threading.Lock()  # activities on a thread pool call back concurrently

    def __call__(self, event):
        line = {"run": event.workflow_run_id, "step": self.step(event) if self.step else _warrant_step(event),
                "tool": event.tool, "args": event.arguments, "decision": event.decision,
                "workflow_id": event.workflow_id}
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(line, default=str) + "\n")


def _warrant_step(event):
    return "+".join(sorted(event.warrant_capabilities))
