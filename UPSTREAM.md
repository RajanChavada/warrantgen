# Issues found while building on Tenuo

Running log of Tenuo and tenuo-cli limits that warrantgen hits, with evidence. Versions: `tenuo` 0.3.2 wheel,
tenuo-cli from the local-cli stack (PRs #942 to #952, tip `f2479877`, isolation fix `cb440d2`).
Status: **open** (not reported), **reported** (link), **fixed** (commit), **by design** (maintainer said so).

## Template format (tenuo-cli YAML, version 2)

| # | Issue | Evidence | Effect on warrantgen | Status |
|---|---|---|---|---|
| T1 | No variables: every value is fixed when the YAML is written | `cli_roundtrip.py`: all 41 issued templates needed the run's repo, branch and PR number filled in | One template per run; can't ship a reusable template | reported #959 |
| T2 | No `all` / `not` | Explore step refused in every run: "under the site folders, minus secrets and `..`" | The step that blocks injected `.env` reads can't go through the CLI | reported #959 |
| T3 | No `subset` | Build step refused in every run (`fixes` from the catalog) | Build can't go through the CLI | reported #959 |
| T4 | `one_of` takes strings only | Emitter refuses numeric one-of | Rare; numbers usually become ranges | open |
| T5 | Plain `"any"` and `"1..5"` strings parse as wildcard and range | `warrant_template.py` `expanded_constraint` | Emitter writes `{exact: ...}` for these | worked around |

Reported as [#959](https://github.com/tenuo-ai/tenuo/issues/959) (pointer on #943). Original ask: `warrant issue --set name=value` for T1, then `all`/`not` and
`subset` forms for T2 and T3. Cite: 41 issued, 2 of 5 seo-agent steps not expressible, and T2 is the step
that stops injected reads.

## SDK

| # | Issue | Evidence | Effect | Status |
|---|---|---|---|---|
| S1 | `Exact` accepts strings only | `TypeError: 'int' object is not an instance of 'str'` minting a PR number | Pinned numbers mint as `Range(n, n)`, as seo-agent's `phases.py` does | worked around |
| S2 | Signed receipts carry tool and request hash, not arguments | `verify_receipt(...).action == "get_order"`, no args field | Drift from receipts is tool-level only; argument drift needs the plaintext trace | by design (privacy) |
| S3 | LangChain and LangGraph have no structured observe hook; Temporal has `audit_callback` and `dry_run`. `auto_protect(mode="audit")` logs without arguments, and `TenuoTool` reports decisions only once a warrant exists | `tenuo/langchain.py` | `record_langchain.Recorder` records through LangChain callbacks instead | worked around |
| S5 | `Pattern` on URLs matches raw text: `https://docs.python.org/3/library/../../internal` passes `Pattern(".../3/library/*")` | Allowed in a test against the real authorizer; `UrlPattern` denies it, plus `%2e%2e` and `host@evil.com` | URL rules mint `UrlPattern` | by design (docs say pattern does not normalize); worked around |
| S4 | Approvals are reusable until they expire | `docs/local-cli.md`; d8joseph: "keeping track of use makes us stateful" | Generated warrants should pair approval gates with an idempotency key argument | by design |

## tenuo-cli (local-cli stack)

| # | Issue | PR | Status |
|---|---|---|---|
| C1 | Issuer and approver keys readable by the agent's user; `init --dev` put them in the project | #942 | fixed in `cb440d2` (`~/.tenuo` default, docs); isolation left to the user, by design |
| C2 | `warrant reissue` brings back a revoked warrant | #944 | reported ([comment](https://github.com/tenuo-ai/tenuo/pull/944#issuecomment-6244548322)), reproduced at `088adb07` and `cb440d2` |
| C3 | `--holder some.pub` resolves from the template's folder, not the current directory | #944 | reported (same comment), reproduced at `088adb07` and `cb440d2` |
| C4 | Approval gates needing 2+ approvers can never pass | #945 | open, traced |
| C5 | `audit verify` accepts receipts the agent forged (signed with its own key) | #950 | open, traced |
| C6 | Agent can write its own warrant file and `receipts.jsonl` (same user, 0600) | #950 | open; probe candidate |
| C7 | Installed `Runtime` roots override `configure()` roots for non-local MCP users | #947 | open, question of intent |
| C8 | `audit list` on a new workspace prints a raw `[Errno 2]` | #950 | open, minor |
| C9 | `approval deny --out other.yaml`: the agent never sees the refusal | #945 | open, minor |

## warrantgen's own limits

- Per-run parameters need at least 2 recorded runs; one run can't tell "always this" from "this run's value".
- Free text is detected by whitespace or length. A short identifier-like free-text value would be pinned.
- Read tools are recognized by name prefix (`read`, `list`, `get`, `fetch`, `search`, `view`); paths by argument name.
- Folder rules take their folders from the run's own calls; a real minter takes them from run settings.
- Fixed: `warrantgen.mint` was both a function and a module (renamed the module to `minting`).
