# Campaign ledger

All campaign state lives in `.seclab/fuzz/`. `fuzz-campaign` owns these
templates; other skills read and write only the headings they name.

Paths in the skills' prose, such as `targets/<id>.md`, are relative to
`.seclab/fuzz/`. Shell commands run from the repository root and spell out
the full path, e.g. `.seclab/fuzz/targets/<id>.scope`.

Rules:
- Update the ledger after every step, before starting the next one.
- `CAMPAIGN.md` stays a short index (about 200 lines at most). Details go
  into the per-target and per-crash files.
- Record facts and decisions, not narration. One line per event.
- Never paste long tool output into the ledger; summarize and link to the
  log file.

## Layout

```
.seclab/
  threat-model.md                 (security-threat-model)
  fuzz/
    CAMPAIGN.md                   index (template below)
    decisions.md                  append-only log of decisions
    bin/                          coverage.py, crash_buckets.py, launcher.py, reach.py (copied by init.py)
    targets/<target>.md           one per target (template below)
    targets/<target>.scope        scope files, one path from the repository root per line (coverage.py --scope)
    crashes/<bucket>.md           one per crash bucket (template below)
    crashes/<bucket>/             inputs/ (at most 10), minimized input, reproducer, regression test, logs
    reports/<id>.md               report drafts
    build/<variant>/              build directories (in-tree-only builds: worktrees under
                                  <checkout>/../<repo>.seclab-worktrees/)
    corpus/<harness>/             persistent corpus per harness (Jest: the package's .cifuzz-corpus)
    dict/<harness>.dict           dictionaries
    oss-fuzz/                     copies of harnesses kept in google/oss-fuzz (baseline only)
    leaks/<harness>.supp          LeakSanitizer suppressions of queued leaks (fuzz-triage)
    coverage/<harness>/r<n>.lcov  snapshots (also r<n>.profdata or r<n>.exec), gate-*.lcov
    coverage/merged/<n>.lcov
    handoff/                      held-back.tsv, PR texts (handoff.md)
    sinks/<target>.tsv            sink sites (id, file, line, group, kind, note)
    runs/<id>/                    one per run (launcher.py): spec.json, meta.json, progress.tsv,
                                  artifacts/copy-<i>/ (findings), copy-<i>/log,
                                  copy-<i>/work/ (scratch; deleted after develop runs);
                                  long runs also corpus/ (AFL++: out/<instance>/)
    generators/                   seed generator scripts
    archive/<date>/               ledger files of earlier campaigns
```

## `CAMPAIGN.md`

```markdown
# Fuzzing campaign: <project>

Resume: invoke `fuzz-campaign` (it runs `init.py`), then the first Next action
(each is `skill: step — target or detail`).

- **Status:** standalone | running | finished
- **Base commit:** <sha> (<branch or tag>), chosen <date>
- **Branch:** seclab/fuzz-<date> | <current branch>
- **Plugin:** seclab-skills <version>; scripts in .seclab/fuzz/bin/
- **First-build notice:** shown <date> | not yet shown
- **Disposable environment:** yes (detected: CODESPACES) | yes (declared) | no

## Settings
| Setting | Value |
|---|---|
| autonomy | interactive |
| report_format | advisory |
| stop_rule | candidates with at least 10% of the attack surface each |
| coverage_goal | <percent of branches in combined scope, or none> |
| gate_seconds | 60 |
| min_exec_per_sec | 100 |
| round_minutes | 1, 2, 4, 8 |
| plateau | two stalled rounds in a row with 100,000 inputs together, or the end of the schedule |
| long_run_hours | 4 |
| memory_mb | 2048 |
| input_timeout_s | 10 |
| sink_groups | 20 |
| sample_size | 10 |

## Coverage
- Started: <date> · Combined scope branch coverage: <x%> (goal <y%> | none)

## Targets
| Target | Entry point | Lang | Status | Scope branches | File |
|---|---|---|---|---|---|
| png-read | png_read_info | c | develop | 12.0% → 41.2% | targets/png-read.md |

Status: `candidate` (ranked), `develop` (picked: harness, gate, inner
loop), `long-run` (`fuzz-coverage` left the loop), `set-aside` (an effort
limit, or a blocking bug without a fix; the reason in its file), `done`
(analysis found nothing left worth the cost). Scope branches: when the
scope was set (the baseline; 0% without one) → now.

## Harnesses
| Harness | Target | Path | Engine(s) | Gate | Workflow |
|---|---|---|---|---|---|
| png_read | png-read | fuzz/png_read_fuzzer.c | libfuzzer, aflpp | pass <date> | read_info + read_image, all transforms |

## Open crashes
| Bucket | Harness | Kind | Verdict | State | Progress | Report | File |
|---|---|---|---|---|---|---|---|

State: the bucket file's (`fuzz-triage`'s verdicts.md). Progress:
`triaged` (verdict assigned), `queued` (its fix waits for the batch,
`fuzz-triage`'s Route), `fixed-locally` (fix.md), `needs-decision` (no
safe fix). Report: `-`, or `drafted` (report.md). Whether the user
reported it is the bucket file's Disclosure.

## Long runs
| Run id | Harness | Command | Tree | Cores | Duration | State | Findings | Analysis |
|---|---|---|---|---|---|---|---|---|
| png_read-long1 | png_read | `python3 .../launcher.py start --spec ...` | worktree 3f2a9c1 | 14 | 24 h | finished | 2 | phase 1 |

State from `launcher.py status`; analysis: no, phase 1, done (`references/analyze.md`).

## Next actions
1. <skill>: <step> — <target or detail>
```

`init.py` rewrites the First-build notice and Disposable environment lines
on every run from what `--notice-shown` and `--disposable` recorded in
git's directory (`git rev-parse --git-path seclab`): editing the lines
changes nothing, and a copied `.seclab/` doesn't carry them.

## `targets/<target>.md`

```markdown
# Target: <id>

- **Entry point:** <function/method/API> [code: path:line]
- **Language:** c | cpp | java | js | ts
- **Status:** <status> · **Rank:** <n> · **Picked:** <date>

## Candidate
- Attacker controls: <...>
- Checks (oracles): <sanitizers, detectors, properties>
- Needs: <setup, prior state>
- How to call: <from tests/examples: path:line>
- Why ranked here: <one line>

## Scope files
<path> — <one-line reason>   (mirror in targets/<id>.scope)

## Sinks
<n of m groups tracked> — details in sinks/<id>.tsv
| Group | Sites | State | Note |

## Harness
One block per harness (a specialist adds one):
- Name/path: <...> · Format: <...> · Engine(s): <...> · Input cap: <bytes>
- Workflow: entry point <...>; call sequence <...>; input selects <...>; checks <...>
- Corpus: corpus/<harness>/ | .cifuzz-corpus/<...> · Dictionary: dict/<harness>.dict
- Build variants: <...> · Tool versions: <compiler, engine, build tool>

Configuration matrix: <harness: the configurations it covers>

## Gate
| Date | Harness | Result | Notes (failed check, core function reached by the seeds, speed, state-leak check) |

## Coverage rounds
| Round | Harness | Duration | Executions | Scope branches | Change | Action before this round (its aim) | Kept |

## Notes
```

## `crashes/<bucket>.md`

```markdown
# Crash bucket: <bucket id>

- **Harness:** <h> · **Kind:** <crash type> · **Top frames:** <a > b > c>
- **Inputs:** <n> (kept: crashes/<bucket>/inputs/; minimized: crashes/<bucket>/minimized)
- **Reproduce:** `<command>`
- **Verdict:** <verdict> · **Confidence:** high | medium | low
- **State:** open | duplicate of <bucket> | non-reproducible | needs investigation
- **Disclosure:** unreported | reported <date, channel> | public
- **Fixed upstream:** no | <commit> (released in <version> | not yet released)

## Evidence
<sanitizer/detector summary, reproduction on base commit, HEAD, latest release>

## Root cause
<what is wrong, where, why the input reaches it> [code: path:line]

## Fix
- Commit: <sha> on <branch> · Validation: <results>
- Review: <answers to fix.md's five questions> · User review: pending | <date>

## Report
- Draft: reports/<id>.md
```

## `decisions.md`

Append one line per decision: `<date> <who: user|agent> <decision> — <reason>`.
