# warrantgen

[![tests](https://github.com/RajanChavada/warrantgen/actions/workflows/tests.yml/badge.svg)](https://github.com/RajanChavada/warrantgen/actions/workflows/tests.yml)

Record what your AI agent actually does, then get the narrowest [Tenuo](https://github.com/tenuo-ai/tenuo) warrant
that still allows it: signed, per step, per sub-agent, with tests that keep it honest in CI. A warrant is Tenuo's
signed token listing which tools a holder may call and with which argument values. Tenuo checks it before every call.

**The problem:** an agent that obeys an injected instruction acts before anyone notices, and a refund, an email or a
pushed commit can't be taken back. Least privilege stops it (each step gets only the tools and values its task needs),
but writing those limits by hand is slow: a real Temporal agent needed ~290 lines of warrant, key and audit code.
warrantgen infers the same limits from recorded runs. On that agent's held-out runs it allows 186/186 legitimate calls
and denies 6/6 injected secret reads.

Try it in five commands, no network or account: [examples/demo](https://github.com/RajanChavada/warrantgen/blob/main/examples/demo/README.md) records a small agent,
infers its warrants, and stops a prompt injection the agent fell for.

For actions that can't be undone, [examples/refund-agent](https://github.com/RajanChavada/warrantgen/blob/main/examples/refund-agent/README.md) caps refunds to recorded
behavior, gates each one behind a signed human approval, and runs 16 failure scenarios: injection, approval bypass,
expired approvals, malformed arguments, timeouts.

Challenge reviewers: [docs/drawerrr-submission.md](https://github.com/RajanChavada/warrantgen/blob/main/docs/drawerrr-submission.md)
maps warrantgen to the "Prevent Irreversible Autonomous Actions" criteria.

## How it works

```mermaid
flowchart TD
    A["Your agent<br/>LangGraph or Temporal"] -- "Recorder<br/>(no warrant needed)" --> T[("traces.jsonl")]
    T --> Q{"Quarantine runs that touched<br/>.env · .. · keys · metadata IPs"}
    Q -- "clean runs" --> I["infer<br/>rules per step"]
    I --> W["Signed warrant<br/>warrantgen.mint"]
    I --> Y["tenuo-cli template<br/>warrantgen template"]
    I --> C["Per-hop delegation chain<br/>warrantgen chain"]
    I --> P["pytest tests<br/>warrantgen test"]
```

1. **Record** tool calls with a LangChain callback or Tenuo's Temporal `audit_callback`. Each call is tagged with its
   run and its step (a LangGraph node path, or the warrant that checked it on Temporal).
2. **Quarantine** any run that touched a secret file, a `..` path or a private host. It is never learned from.
3. **Infer** one rule per argument from several runs: what's fixed, what changes per run, what's free text.
4. **Use** the rules: mint a warrant, write a `tenuo-cli` template, mint a delegation chain, or generate tests.

## Safety approach

Tenuo checks every tool call against the warrant before the tool runs, so a denied call never happens. It checks
signed values, not intent: an LLM judging each call reads the same injected text as the agent, a warrant doesn't.

| A call to a tool that can't be undone | Outcome |
|---|---|
| Inside the recorded limits | Runs only with a human's signed approval, bound to these exact arguments |
| Outside the recorded limits | Denied before the human is asked, so an approval can't widen it |
| A tool the step never used | Denied: it isn't in the warrant |

- **Deny by default:** a step's warrant lists only the tools its recorded runs used, each argument limited to what
  those runs passed (this ticket's order, a refund range, a docs folder).
- **Approval gates:** `mint(gates=["issue_refund"], approvers=[...])` makes each call wait for a signed approval tied
  to its exact arguments. No answer fails closed.
- **Short-lived:** warrants and approvals expire, and expired ones are denied.
- **Sub-agents only narrow:** each sub-agent's warrant is delegated from its parent's and Tenuo rejects any widening,
  so a hijacked sub-agent can't use a sibling's tools.
- **Poisoned runs aren't learned:** quarantine drops any run that touched secrets, `..` paths or private hosts.
- **Drift fails CI:** generated tests fail if a warrant stops allowing recorded calls or starts allowing near-misses.

warrantgen builds the rules, warrants, chains and tests. Tenuo provides the signing, the authorizer, approval
signatures and delegation checks.

## What inference looks like

Rules inferred from 15 recorded runs of a real agent on 3 sites, next to the warrant its authors wrote by hand
(the `fetch.url` row comes from the LangGraph example):

| Argument | Recorded values | Inferred rule | Hand-written warrant |
|---|---|---|---|
| `repo` | one per run: `acme/site`, `northside/bakery-web`, ... | per-run value → `Exact` | `Exact(repo)` |
| `path` | this run's files | per-run list → `OneOf` | `OneOf(paths)` |
| `branch` | `fixagent/agent-20261018-014809`, ... | per-run value (or `Pattern("fixagent/*")`) | `Pattern("fixagent/*")` |
| `size` | 1 to 503 | `Range(0, 1000)` (rounded headroom) | `Range(max=20000)` |
| `text`, `message`, `body` | free text | `Wildcard` | `Wildcard` |
| `read_file.path` | files under `public/`, `web/` | under those folders, minus secrets and `..` | the same `All([...])` shape |
| `fetch.url` | pages on `docs.python.org/3/library/` | `UrlPattern(".../3/library/*")` | n/a |

Reads generalize to their folders or URL prefix (several hosts become `AnyOf` of one `UrlPattern` per host); writes keep
their exact files. Money-like arguments (`amount`, `price`, `cents`, ...) never get headroom.

## Per-hop delegation chains

When a LangGraph supervisor hands work to sub-agents, `warrantgen chain` builds one warrant per hop from the recorded
node paths. Every child is minted from its parent through Tenuo's own delegation API, so Tenuo checks that each hop only
narrows.

```mermaid
flowchart TD
    R["root, signed by the issuer<br/>search · fetch · write_file"]
    R --> T["team<br/>search · fetch"]
    T --> S["team/researcher<br/>search · fetch under docs.python.org/3/library/"]
    R --> W["writer<br/>write_file: this run's report only"]
    S -.-x SD["write_file: denied"]
    W -.-x WD["fetch: denied"]
```

Tested under real `TenuoTool` enforcement with full chains: each sub-agent runs its recorded calls, and none can use a
sibling's tools. If Tenuo refuses to narrow an argument, it keeps its parent's constraint and `chain` says which.
Since tenuo 0.3.6 that includes a URL rule listing several hosts: every child keeps the parent's full host list.

## Evidence

39 tests run in CI on Python 3.10 and 3.13, against tenuo 0.3.6 and 0.3.7.

### Refund agent: irreversible actions

A new ticket's order note tells the agent to refund 500000 cents and email the confirmation to an attacker, and the
agent obeys. All 16 scenarios below come from `support_agent.py scenarios` and are checked by `tests/test_refund_demo.py`
([examples/refund-agent](https://github.com/RajanChavada/warrantgen/blob/main/examples/refund-agent/README.md)).
The 2 legitimate calls (an approved in-range refund, an email to this customer) run.

| Failure state | Attempt | Result |
|---|---|---|
| Prompt injection | $5,000 refund, even with the lead's approval | denied: above the 3450-cent cap |
| Prompt injection | email the confirmation to the attacker | denied: not this ticket's customer |
| Unauthorized request | close the customer's account | denied: never recorded, not in the warrant |
| Edge case | refund another customer's order | denied: not this ticket's order |
| Malformed arguments | amount `"lots"`; negative amount | rejected by the tool's schema; denied by the range |
| Approval bypass | no approval; the agent signs its own; an approval replayed for a bigger amount | denied |
| Approval bypass | hand the refund to another key | denied: the delegated warrant keeps the gate |
| Expiry | approval expired a minute ago; warrant past its lifetime | denied |
| Timeout | approver never answers | fails closed |
| **Known gap** | same approval reused for the identical refund | **allowed** (see Limitations) |

Minting the warrant without its gate makes 6 of the 16 rows fail, so the gate is what blocks them.

### Research agent: prompt injection

A docs page tells the agent to fetch the cloud metadata endpoint. The inferred
`UrlPattern("https://docs.python.org/3/library/*")` denies it
([examples/demo](https://github.com/RajanChavada/warrantgen/blob/main/examples/demo/README.md)).

### A real agent

The test agent is a Temporal workflow that opens pull requests to fix a website, minting one warrant per phase. 15
recorded runs: 3 sites × 5 behaviors, including a prompt injection that tries to read `.env` and
`../payments-api/.env`. Details in [examples/seo-agent](https://github.com/RajanChavada/warrantgen/blob/main/examples/seo-agent/README.md).

| Check | Result |
|---|---|
| Held-out replay (infer from 14 runs, replay the 15th through Tenuo's authorizer) | **186/186** legitimate calls allowed, **6/6** injected reads denied |
| Same replay with read paths deliberately unconstrained | 0/6 denied (the replay can fail) |
| Through `tenuo-cli` (`template validate` + `warrant issue`) | 41 templates issued, 87/87 calls allowed; 2 of 5 steps need template features proposed in [tenuo#959](https://github.com/tenuo-ai/tenuo/issues/959) |
| Generated tests against the agent's **hand-written** warrants | 40/44 at first: 4 places looser than recorded behavior. After pinning the branch: 43/44, `size` kept loose on purpose |

## Quickstart

```bash
pip install "warrantgen[langchain]"
```

Record (several runs with different inputs: one run can't tell a fixed value from a per-run one):

```python
# LangChain or LangGraph
from warrantgen.record_langchain import Recorder
graph.invoke(state, {"callbacks": [Recorder("traces.jsonl")]})

# Temporal, through Tenuo's plugin (dry_run=True with a broad warrant to observe)
from warrantgen.record_temporal import TemporalRecorder
TenuoPluginConfig(..., audit_callback=TemporalRecorder("traces.jsonl"))
```

Then:

```bash
warrantgen infer traces.jsonl                          # rules per step, plus quarantined runs
warrantgen template traces.jsonl --step write --set repo=acme/site --set paths=a.txt,b.txt --out write.yaml
tenuo-cli warrant issue --template write.yaml --holder agent
warrantgen chain traces.jsonl --from-run RUN           # per-hop tree, minted and checked by Tenuo
warrantgen test traces.jsonl --out test_warrants.py    # recorded calls allowed, near-misses denied
```

In Python: `warrantgen.infer`, `warrantgen.mint`, `warrantgen.chain.mint_chains`. Pass `gates=["issue_refund"]` and
`approvers=[key.public_key]` to `mint` to require a signed approval on every call to those tools.

### Test the warrants you ship

```bash
warrantgen test traces.jsonl --warrant-for my_warrants:warrant_for --known-looser size --out tests/test_recorded_calls.py
```

The generated file imports your `warrant_for(step, values, issuer, holder_public_key)` instead of warrantgen, so you can
commit it with only `tenuo` and `pytest` as dependencies. Each step's recorded calls must be allowed, and near-misses
must be denied: one argument just outside its rule, or a tool the step never used. `--known-looser` marks arguments you
loosen on purpose as strict expected failures, so tightening one later flags the stale mark.

## Rules

| Rule | Inferred when | Tenuo constraint | Generated near-misses |
|---|---|---|---|
| exact | one value everywhere | `Exact` (numbers: `Range(n, n)`) | another value |
| per-run value | one value per run, different across runs | `Exact`, filled at mint time | another value |
| per-run list | a list that changes per run | `OneOf`, filled at mint time | a value not in the list |
| range | numbers | `Range`, rounded headroom | max + 1 |
| folders | path argument of a read tool | `All([folders, Not(secret), Not(..)])` | `.env`, `../`, another folder |
| URL prefix | URL argument of a read tool | `UrlPattern`, or `AnyOf` per host | another host, `..` climb |
| prefix | strings sharing a `/`-delimited prefix | `Pattern` | outside the prefix |
| subset | lists of items | `Subset` | an item outside the set |
| any | free text (whitespace or long) | `Wildcard` | none |

Override a guess with `--var ARG` (treat as per-run) or `--exact ARG` (pin it).

## Limitations

- **Approval reuse:** an approval works for identical arguments until it expires, so a refund could be paid twice in
  that window. Tenuo treats this as by design. Fix: an idempotency key the payment API refuses to repeat.
- **Harm inside the limits:** a 3000-cent refund on the right order passes if the human approves it.
- **Poisoned recordings:** quarantine catches secrets, `..` and private hosts, not a $5,000 refund that looks normal.
  Record from tasks you trust and review `warrantgen infer` output before shipping.
- **Gates are chosen by hand:** warrantgen doesn't infer which tools can't be undone.
- **Free text is unconstrained:** arguments like an email body get `Wildcard`.
- **Ambiguous or missing information:** the warrant caps the damage; it doesn't make the agent stop and ask.
- **Needs 2+ runs per task:** one run can't tell a fixed value from a per-run one.
- **Multi-host URLs don't narrow per hop:** since tenuo 0.3.6, sub-agents keep the parent's full host list.
- **Traces are plaintext:** they hold raw tool arguments, which can include customer data or secrets. Don't commit them.
- **Early:** version 0.x, not independently audited. The demos script the model and the approver.

## Status

Early and opinionated. Tested against tenuo 0.3.6 and 0.3.7 (the versions the pin allows) and the local-cli stack
(tenuo#942 to #952).
Limits and the Tenuo issues found along the way are tracked in [UPSTREAM.md](https://github.com/RajanChavada/warrantgen/blob/main/UPSTREAM.md).

## License

Apache-2.0
