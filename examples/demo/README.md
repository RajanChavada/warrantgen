# Demo: from five runs to a warrant that stops a prompt injection

`agent.py` is a small LangGraph research agent (a researcher subgraph that searches and fetches, a writer that
saves the report). The model is scripted and nothing touches the network. Run 4 fetches a docs page with an
instruction hidden in it, and the agent obeys: it fetches the cloud metadata endpoint.

From the repo root, after `uv sync`:

```bash
# 1. Run the agent five times; each run's tool calls land in traces/run-N.jsonl
uv run python examples/demo/agent.py record traces

# 2. Infer one rule per argument, per step. Run 4 is quarantined and never learned from
uv run warrantgen infer traces/*.jsonl

# 3. Check a loose draft against the runs: tighten fetch.url, flag search.limit, keep delete_file as untested
uv run warrantgen reconcile traces/*.jsonl --draft examples/demo/draft.yaml --step team/researcher --out proposal.yaml

# 4. Generate tests: recorded calls allowed, near-misses denied
uv run warrantgen test traces/*.jsonl --out test_warrants.py && uv run pytest -q test_warrants.py

# 5. Mint the warrants and run two new tasks under Tenuo: the legitimate one passes, the poisoned fetch is denied
uv run python examples/demo/agent.py enforce traces
```

`tests/test_demo.py` runs the same steps, so this page stays accurate.
