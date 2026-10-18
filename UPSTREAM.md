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
| T6 | No `any_of` | Multi-host fetch rules (`AnyOf` of `UrlPattern`s) are refused by the emitter | Steps that fetch from several hosts can't go through the CLI | open (add to #959) |
| T5 | Plain `"any"` and `"1..5"` strings parse as wildcard and range | `warrant_template.py` `expanded_constraint` | Emitter writes `{exact: ...}` for these | worked around |

Reported as [#959](https://github.com/tenuo-ai/tenuo/issues/959). Maintainer answer: yes, as three PRs after the CLI stack
merges, in this order and shape:
1. `subset`, mapping to `Subset`.
2. `all` / `not` with core semantics. For paths the allowlist must be `subpath`, because a negated regex doesn't
   normalize paths; `not` is for value exclusions only.
3. Per-issue values as `--set name=value` that **narrows** an argument the template already bounds (one-of,
   subpath, pattern) to an exact value, checked by core attenuation. No unbounded `{var}`; the template is the ceiling.

What this changes in warrantgen: the folder rule should become `All([Subpath(root), Not(Regex(secret))])` once
paths are absolute, and per-run values should be emitted as a bounded ceiling plus `--set`, not as a variable.

Original ask: `warrant issue --set name=value` for T1, then `all`/`not` and
`subset` forms for T2 and T3. Cite: 41 issued, 2 of 5 seo-agent steps not expressible, and T2 is the step
that stops injected reads.

## SDK

| # | Issue | Evidence | Effect | Status |
|---|---|---|---|---|
| S1 | `Exact` accepts strings only | `TypeError: 'int' object is not an instance of 'str'` minting a PR number | Pinned numbers mint as `Range(n, n)`, as seo-agent's `phases.py` does | worked around |
| S2 | Signed receipts carry tool and request hash, not arguments | `verify_receipt(...).action == "get_order"`, no args field | Drift from receipts is tool-level only; argument drift needs the plaintext trace | by design (privacy) |
| S3 | LangChain and LangGraph have no structured observe hook; Temporal has `audit_callback` and `dry_run`. `auto_protect(mode="audit")` logs without arguments, and `TenuoTool` reports decisions only once a warrant exists | `tenuo/langchain.py` | `record_langchain.Recorder` records through LangChain callbacks instead | worked around |
| S6 | `assert_authorized(w, key, tool, args)` called without `with` checks nothing and passes; its docstring shows that bare form | A bare call with arguments the warrant denies passed, with and without `TENUO_TEST_MODE` | Generated tests always use `with` | open, worth reporting |
| S7 | `tenuo.testing` detects pytest only if `"pytest"` is in `PYTEST_CURRENT_TEST` (the test's path), so tests outside such a path raise `RuntimeError` | Same test file: fails from `/private/tmp/...`, passes under `pytest-of-*` | Generated tests set `TENUO_TEST_MODE=1` | open, worth reporting |
| S5 | `Pattern` on URLs matches raw text: `https://docs.python.org/3/library/../../internal` passes `Pattern(".../3/library/*")` | Allowed in a test against the real authorizer; `UrlPattern` denies it, plus `%2e%2e` and `host@evil.com` | URL rules mint `UrlPattern` | by design (docs say pattern does not normalize); worked around |
| S4 | Approvals are reusable until they expire | `docs/local-cli.md`; d8joseph: "keeping track of use makes us stateful" | Generated warrants should pair approval gates with an idempotency key argument | by design |

## tenuo-cli (local-cli stack)

| # | Issue | PR | Status |
|---|---|---|---|
| C1 | Issuer and approver keys readable by the agent's user; `init --dev` put them in the project | #942 | fixed in `cb440d2` (`~/.tenuo` default, docs); isolation left to the user, by design |
| C2 | `warrant reissue` brings back a revoked warrant | #944 | fix PR [#960](https://github.com/tenuo-ai/tenuo/pull/960), requested by the maintainer |
| C3 | `--holder some.pub` resolves from the template's folder, not the current directory | #944 | fix PR #960 |
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
- Fixed: a parent hop fetching from several hosts used to get an exact per-run URL list its children couldn't
  narrow. It now gets `AnyOf` of one `UrlPattern` per host, and children narrow to `AnyOf([their host])`
  (Tenuo refuses `AnyOf` to a bare `UrlPattern`: "Cannot attenuate Any to UrlPattern").
- Mixed schemes (an `ftp://` URL next to `https://` ones) still give a per-run list, which children keep.
- Tenuo narrows an `All` only by adding parts ("child All must include all parent constraints"), so a child's
  folder rule is the parent's `All` plus one more folder regex. Works, but the child's constraint grows per hop.
