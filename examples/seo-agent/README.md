# seo-agent dogfood

Traces from 15 runs of seo-agent's `FixRunWorkflow` (3 sites x 5 agent routes) on a real local Temporal server
with Tenuo's plugin checking every activity. GitHub and the model are scripted; no network.

- `record_runs.py`: copy into seo-agent's `tests/` and run `uv run python tests/record_runs.py traces.jsonl`.
- `evaluate.py`: leave-one-run-out replay through Tenuo's authorizer.

Result on 2026-10-17: 182/186 legitimate calls allowed on unseen runs, 6/6 injected `.env` reads denied.
The 4 misses are files only one run per site read; `phases.py` covers them with a rule derived from the site roots.
