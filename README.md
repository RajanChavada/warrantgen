# warrantgen

Record what your agent does, then get the narrowest [Tenuo](https://github.com/tenuo-ai/tenuo) warrant that still allows it.

## Record

LangChain or LangGraph (no warrant needed yet):

```python
from warrantgen.record_langchain import Recorder

graph.invoke(state, {"callbacks": [Recorder("traces.jsonl")]})
```

Temporal, through Tenuo's plugin (use `dry_run=True` with a broad warrant to observe):

```python
from warrantgen.record_temporal import TemporalRecorder

TenuoPluginConfig(..., audit_callback=TemporalRecorder("traces.jsonl"))
```

Record several runs with different inputs: one run can't tell a fixed value from a per-run one.

## Infer and issue

```bash
warrantgen infer traces.jsonl                    # rules per step, plus quarantined runs
warrantgen template traces.jsonl --step write --set repo=acme/site --set paths=a.txt,b.txt --out write.yaml
tenuo-cli warrant issue --template write.yaml --holder agent
```

- A step is a LangGraph node path (`team/researcher`) or, on Temporal, the set of tools on the checking warrant.
- Runs that touched secrets, `..` or private hosts are quarantined, never learned.
- Reads generalize to their folders (minus secrets) or URL prefix; writes keep their exact files.
- Ranges get rounded headroom (`--headroom 2`, or `none`); money-like arguments never do.
- `warrantgen chain traces.jsonl --from-run RUN` shows the per-hop tree (a LangGraph subgraph `team/researcher`
  sits under `team`, under the root) and mints it through Tenuo's delegation API, so Tenuo checks every hop narrows.
  Each sub-agent gets only its own tools; an argument Tenuo won't narrow keeps its parent's constraint and is listed.
  In Python: `warrantgen.chain.hop_rules`, `hop_values`, `mint_chains`.
- `template` exits 2 with the reasons when a step needs something tenuo-cli templates can't express yet
  ([tenuo#959](https://github.com/tenuo-ai/tenuo/issues/959)). In Python, `warrantgen.mint` mints any step directly.

## Status

Early. Results on a real Temporal agent are in [examples/seo-agent](examples/seo-agent/README.md);
Tenuo limits found along the way are in [UPSTREAM.md](UPSTREAM.md).
