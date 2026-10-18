# seo-agent dogfood

Traces from 15 runs of seo-agent's `FixRunWorkflow` (3 sites x 5 agent routes) on a real local Temporal server
with Tenuo's plugin checking every activity. GitHub and the model are scripted; no network.

- `record_runs.py`: copy into seo-agent's `tests/` and run `uv run python tests/record_runs.py traces.jsonl`.
- `evaluate.py`: leave-one-run-out replay through Tenuo's authorizer.

Results (2026-10-17), legitimate calls allowed on unseen runs / injected `.env` reads denied:

| Version | Legit | Attacks |
|---|---|---|
| Exact file lists | 182/186 | 6/6 |
| Reads generalize to their folders, minus secrets and `..` | 186/186 | 6/6 |
| Mutation check: reads unconstrained | 186/186 | 0/6 |

The mutation row shows the replay can fail: the 6/6 comes from the folder rule.
