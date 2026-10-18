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

## Through tenuo-cli

`cli_roundtrip.py` writes each held-out run's steps as a version 2 template, then `tenuo-cli template validate`
and `warrant issue` them, and replays the run against the issued warrant file. Set `TENUO_CLI` to the command
that runs the CLI. Sample output: `write-step.example.yaml`.

Result (2026-10-17): 41 templates issued, 87/87 legitimate calls allowed. Two steps are refused in every run
rather than written looser than inferred:

| Step | Needs | Why the template format can't hold it |
|---|---|---|
| explore | all/not | "under the site folders, minus secret files and `..`" |
| build | subset | the run's fixes are a subset of the catalog |

Every step also needs this run's values filled in (repo, branch, PR number), because templates have no variables.
Explore is where the injected `.env` reads happen, so the CLI path can't yet protect the step that matters most.
