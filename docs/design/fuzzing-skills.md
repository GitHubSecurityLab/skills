# Fuzzing skills: design

This document records the decisions behind the fuzzing skills and their reasons; the procedures are in the skills.

## Contents

1. [Purpose and scope](#1-purpose-and-scope)
2. [Principles](#2-principles)
3. [How the parts fit together](#3-how-the-parts-fit-together)
   - 3.1 [Campaign workflow](#31-campaign-workflow)
   - 3.2 [Skills](#32-skills)
   - 3.3 [Scripts](#33-scripts)
   - 3.4 [Interactive and autonomous mode](#34-interactive-and-autonomous-mode)
4. [Campaign state and git](#4-campaign-state-and-git)
5. [Safety](#5-safety)
6. [Threat model and targets](#6-threat-model-and-targets)
7. [Harnesses and the gate](#7-harnesses-and-the-gate)
8. [Coverage loop](#8-coverage-loop)
9. [Seeds and corpora](#9-seeds-and-corpora)
10. [Long runs and analysis](#10-long-runs-and-analysis)
11. [Triage and fixes](#11-triage-and-fixes)
12. [Verdicts, reports and hand-off](#12-verdicts-reports-and-hand-off)

---

## 1. Purpose and scope

The skills help users run effective fuzzing campaigns on C/C++, Java and JS/TS code. They split a campaign into a workflow of steps, including threat modeling, target selection, fuzz harness creation, coverage analysis and crash triage, and describe how to do each step well. An effective campaign fuzzes the code an attacker can reach, with harnesses checked to reach it; it finds out why code stays uncovered instead of only measuring coverage; and it turns crashes into root causes, fixes and reports that maintainers can act on. The agent does the work. The user makes the decisions where a wrong call is expensive, and runs the long fuzzing runs. The skills fuzz in-process, through library and API entry points.

Security researchers auditing third-party code come first; maintainers fuzzing their own code use the same workflow. Its defaults are the researcher's: the code is untrusted (§5), and coordinated disclosure is assumed (§12). There are no researcher and maintainer modes, because every mode difference had a mode-free answer.

## 2. Principles

Three principles shape the decisions in the rest of this document:
- **Method first.** Guardrails limit what the agent may do: it never pushes, and it holds back anything that may reveal a vulnerability. Evidence checks make it show proof before it moves on: a harness must reach its target before long runs, and a harness change stays only if it adds coverage. Both prevent mistakes, but finding bugs comes from doing each step well, so that's where the skills put their weight.
- **Portable.** The skills use only features that GitHub Copilot CLI, Claude Code and OpenAI Codex share: no agent-specific frontmatter, subagents or pre-approved tools, and no hooks, such as enforcement hooks that stop the agent from finishing.
- **A campaign outlives a session.** Long runs take hours or days, their analysis comes in a later session, and a long conversation gets compacted and loses detail. So the state lives in files (§4), long runs run on their own and belong to the user (§3.1, §10), and any session can continue a campaign.

## 3. How the parts fit together

### 3.1 Campaign workflow

A campaign moves through four stages:
1. **Setup (agent):** measure the existing harnesses, write the threat model and rank the targets.
2. **Develop (agent), per target:** write or extend a harness, check it (§7) and fuzz it in short rounds. At a coverage plateau, find out why code stays uncovered and take the next action (§8). Crashes go to triage (§11).
3. **Long runs (user):** the agent prepares the commands, and the user runs them in a terminal. Agents can't wait hours, a run an agent starts may end with its session, and waiting would stall develop.
4. **Analysis (agent, when the user asks):** triage, fixes and reports, corpus merges and coverage. It leads back to develop for coverage gaps, to longer runs while coverage still grows, or, once the user is done, to upstreaming the harnesses.

`fuzz-campaign` decides the order; the other skills hold the steps.

The user ends the campaign, or agrees after an analysis that finds no further work. Develop running out of actions leads to long runs and their analysis, not to the end.

### 3.2 Skills

The skills are split by what users ask for, and each one works inside a campaign or on its own:
- **Skills by user intent.** Four workflow skills, the shared threat model and one reference skill per language. Each common request maps to one skill ("write a fuzz test", "why is coverage stuck?", "triage this crash"), and steps every campaign uses together share a skill. Not one skill per step, language or engine, because the method would scatter across them, nor one large skill, because single-step requests would stop triggering it.
- **Each workflow skill owns its steps** in its `references/` and also works on its own. A missing input is never a reason to start a whole campaign: the skill does a focused version of the missing step (a threat model for one entry point, a coverage build).
- **Call and return.** A skill invoked by another does the one step it's asked for, writes its evidence and returns. On a standalone request, it stops when the request is done and offers the next step without starting it. Without this rule, a nested skill could follow its own next step instead of returning to its caller.

The skills and their roles:

| Kind | Skill | Role |
|---|---|---|
| Workflow | `fuzz-campaign` | Runs the campaign: setup, choosing targets, the order of the steps, long-run commands and their analysis, upstreaming the harnesses, and the campaign's record |
| | `fuzz-harness` | Writes a harness, builds it, adds seeds and a dictionary, checks that it works, and runs it in short rounds |
| | `fuzz-coverage` | Measures coverage, finds out why code stays uncovered, and applies the next action: seeds, a dictionary, targeted inputs or a harness change |
| | `fuzz-triage` | Reproduces, groups and minimizes crashes, finds the root cause, decides what kind of bug it is, fixes it and drafts the report |
| Shared | `security-threat-model` | Maps where untrusted data enters, the dangerous operations it can reach, and the paths between them |
| Language | `fuzz-c-cpp` | Reference for libFuzzer, AFL++ and FuzzTest |
| | `fuzz-java` | Reference for Jazzer |
| | `fuzz-js` | Reference for Jazzer.js (JavaScript and TypeScript) |

### 3.3 Scripts

The scripts live in `fuzz-campaign/scripts/`. `init.py` copies the others into the campaign's directory, `.seclab/fuzz/bin/` (§4): commands get a short, stable path, and a long run keeps working when a plugin update replaces the installed files.

| Script | Role |
|---|---|
| `init.py` | Sets up the campaign's files in `.seclab/fuzz/` and prints the safety rules; every workflow skill runs it first |
| `launcher.py` | Runs every command that executes fuzzed code (fuzzing runs, corpus replays and merges, crash reproductions), each in a minimal environment and its own scratch directory, and stops it together with every process it started (§5) |
| `coverage.py` | Turns coverage reports into what the agent needs: coverage of the target's files, branches never taken, annotated source, comparisons between measurements, and which dangerous operations were reached; converts Java's JaCoCo reports too |
| `crash_buckets.py` | Groups crash reports that are likely the same bug, by the top stack frames in the project's code |
| `reach.py` | Finds the corpus inputs that reach a given line, as starting points for inputs aimed at that line |

Three decisions shape how the scripts run:
- **Scripts never decide what to run:** the launcher and `reach.py` run only the commands the agent passes.
- **`init.py` starts every workflow skill,** found by its path next to the skill, since all three agents show each skill's directory and install the whole plugin. Not an Initialize section in `fuzz-campaign`, because invoking that skill for it could cascade into a full setup, and standalone skills missed the first-build notice and the safety rules (§5).
- **Plugin installs only.** A skill installed on its own has no `../fuzz-campaign`, so it asks the user to install the plugin. `init.py` finds the plugin by the `plugin.json` that lists it, so a repository that holds copies of the skills isn't taken for the plugin.

### 3.4 Interactive and autonomous mode

A campaign runs in one of two modes, and both use the same rule to decide when to stop adding targets:
- **Interactive (default):** the agent works on its own and asks the user only where a wrong call is costly or the decision is the user's: before building or running any project code (the setup card), before changing the project's build files, when choosing targets, when the long-run commands are ready, and before a report is finished. The setup card bundles every setup decision into one question (the first-build notice, branch, base commit, install plan, and the command that gives the agent access to the builds outside the checkout), so setup costs the user a single answer.
- **Autonomous (only when the user asks):** the agent needs full permissions, which a skill can't grant. It takes the top candidate at every choice and logs it, then hands over the long-run commands and stops. The safety rules still apply.
- **When to stop adding targets:** at the user's limit (a number of targets, a time box), or else after the candidates that each reach at least about 10% of the uncovered attack-surface code of all candidates together, within a time cap; interactive mode proposes this rule at the targets checkpoint. Every harness costs about the same effort, so the largest candidates get the most attack surface into long runs soonest. The rule only ends the first pass: the other candidates stay ranked and compete again whenever an analysis sends work back to develop. Coverage never decides when to stop, since 100% isn't reachable.

## 4. Campaign state and git

A campaign's state lives in files in the project, and its changes to the project's code in git:
- **Plain files in the project, not a database.** `.seclab/` at the project's root holds the skills' state: `threat-model.md`, which every topic shares, and one directory per topic. For fuzzing, that's `.seclab/fuzz/`, with the campaign's ledger, `CAMPAIGN.md`, a short index of its state and next actions. Agents and users read Markdown; the scripts read numbers from lcov coverage files and tab-separated tables. Git's exclude file lists `.seclab/`, so the project's `.gitignore` stays untouched.
- **The ledger drives resuming.** It's updated after every step, and its next actions each name a skill and a step. After the conversation is compacted, the agent invokes those skills again, since compaction drops skill text loaded early.
- **One writer:** one campaign and one session at a time per repository, since the ledger has a single writer. A new campaign reuses the old one's corpora, dictionaries, threat model and known crash groups.
- **A branch of its own:** `seclab/fuzz-<date>` at the base commit (the checkout's HEAD or the latest release), or the current branch if the user chooses. Not at the default branch's HEAD, because switching there can remove the code the user asked to fuzz. Commit types keep harness, build, fuzzing-mode and fix commits apart, so hand-off branches can take the harnesses without the fixes (§12).
- **Skills used on their own** leave a minimal workspace that a later campaign reuses.

## 5. Safety

The skills give no security guarantee: their rules steer the agent, and their scripts enforce some protections when the agent uses them. The only boundary is the environment, a codespace or VM without credentials that matter. Within that, the skills guard against three risks: the project's code runs on the user's machine, the project's text could steer the agent, and a finding could become public before it's fixed. `init.py` prints the safety rules for every skill that builds or runs project code, since only those rules reach every step; the references that hold the commands are read only by the steps that use them.

**The project's code runs on the user's machine.** Before any of it runs, a first-build notice says what will run and the risks, and recommends a codespace or VM for code the user doesn't trust. Everything that executes fuzzed input runs through the launcher. It gives each process an empty environment apart from `PATH`, `LANG` and what the language skill passes, so tokens and agent sockets aren't passed to it; its own scratch directory as working directory, `HOME` and `TMPDIR`; and its own process group, so stopping a run stops everything it started. Builds, installs, the project's tests and bisects run outside the launcher, with the user's environment. Memory and per-input time are capped with engine flags, not process limits: `RLIMIT_AS` breaks ASan's shadow memory and the JVM's and V8's mappings, and Linux and macOS ignore `RLIMIT_RSS`. There's no `sudo` and no system change on the user's own machine, even in autonomous mode; only a disposable environment allows them.

**The project's text could steer the agent.** Repository content and everything the target prints, which echoes attacker-controlled input, are data, never instructions; only tails and summaries reach the agent. The agents still load the project's instruction files; the README names the options that turn this off.

**A finding could become public before it's fixed.** The skills never push, open issues or pull requests, or post crash details, and they hold back what reaches or reveals a possible vulnerability (§12).

The protections have known gaps:
- **No confinement.** The fuzzed code can read any file, find and use the ssh-agent socket, write to absolute paths and use the network, unless a Java or JS detector blocks it. OS-level confinement would need profiles per engine and ecosystem, tested inside agents that already sandbox.
- **Not every resource is capped:** memory isn't for AFL++'s ASan instance, a JVM beyond its heap, or FuzzTest on macOS, and disk isn't at all.
- **Detectors run real code.** They report only what they recognize; any other dangerous call really runs.

## 6. Threat model and targets

The threat model says what an attacker controls and what it can damage:
- **The agent writes the threat model; the user corrects it.** A user, even a maintainer, isn't necessarily a security expert. The agent works from the code, the docs and the project's past security fixes, and records where each entry came from, so the user sees what's a guess. The user's corrections ("configuration is trusted") take precedence.
- **It's code-level:** sources (where untrusted data enters), sinks (dangerous operations such as memory writes, file access or command execution) and the paths between them. This is what a harness needs: it feeds fuzzed data in at a source and aims to reach the sinks along the paths. The attacker and deployment context decides which data counts as attacker-controlled and how serious a bug is (§12). It lives in `.seclab/threat-model.md` and doesn't depend on fuzzing, so later topics can reuse it.
- **Sinks are found with per-language catalogs,** lists of dangerous APIs such as Java's `Runtime.exec` or JavaScript's `eval`, by reading and searching the code. For C/C++ memory safety, listing every buffer access would bury the dangerous ones, so the threat model records hotspots instead: the files and functions with the most size arithmetic on attacker data.

Targets are chosen from the threat model and from measured coverage:
- **Existing fuzzing is measured first.** Existing harnesses are built and their corpora replayed, so ranking knows what's already covered. When an existing harness calls into code it doesn't fully cover, the agent improves that harness (seeds, a dictionary, a harness change) instead of writing a second harness for the same entry point.
- **Three gaps that coverage reports hide are looked for too.** A module no harness builds doesn't appear in them at all, not even as 0%. A fuzzed function can have unfuzzed variants, such as `parse_stream()` next to a fuzzed `parse(buf, len)`, that run the same code under other conditions. And a harness that calls a serializer without checking its output finds only crashes, where a round-trip check would also find wrong results.
- **Every candidate is ranked cheaply; only the top few are analyzed in depth.** Candidates are entry points that take attacker data. Those the threat model says an attacker can't reach are dropped; the rest are ranked from rough estimates by the uncovered attack-surface code they reach, then bug likelihood and the harness's cost. Only the top few get the costly part, reading the code for their scope files (the files that handle their data) and sinks, which may reorder them.
- **Coverage is measured on a target's scope files only:** the files that handle the data its harness feeds in, such as the decoder files for a PNG harness. Whole-project numbers mislead both ways: doubling a 2,000-line parser's coverage barely moves a 200,000-line project's number, so progress looks like a standstill, while CLI, logging or test code the harness happens to run looks like progress. The scope starts as an estimate from reading the code and is corrected once coverage stops growing. Attack-surface files the harness reaches are added. A file still at 0% stays if some action could reach it, and is removed if it can't be reached, since it would keep the number low whatever the harness does.

## 7. Harnesses and the gate

A harness should be one the project would merge and should find bugs that matter:
- **Written like the project's tests:** same framework, location, naming and build system, so maintainers can adopt it.
- **Realistic input.** Fuzzed data enters only where an attacker's data enters, and sets options only when an attacker can set them. Documented API contracts are respected, and features stay on as deployments have them. Breaking a contract finds bugs nobody will fix, while switching a feature off, stubbing it or catching every exception hides real ones.
- **Bugs are caught by sanitizers and the engines' detectors.** Checks beyond crashes, such as round trips or a comparison with another implementation, must be able to fail and assert only what the spec or docs guarantee, since unfounded checks flooded fuzzing with false positives.
- **Extend before adding.** Code an existing harness already exercises gets that harness extended. A second harness for the same code stays only if it adds coverage or something new: a configuration, state, call order, input format or check.

Every harness change is built and must pass the gate before it fuzzes. The gate is evidence that the harness works, and checks that:
- **the target library is instrumented,** not just the harness, since otherwise the engine fuzzes blind;
- **the seeds reach the target's code before any fuzzing,** since a harness that feeds input wrongly still reaches the entry point and a few functions after a minute of fuzzing;
- **fuzzing gets past the seeds** in a new harness's first run, since no progress means mutations don't get past an early check;
- **the seeds don't crash it,** since such a bug blocks fuzzing (§11);
- **no state leaks between inputs,** since a crash that depends on earlier inputs doesn't reproduce;
- **it respects the threat model,** checked with a checklist.

A harness written by a skill used on its own skips the last two checks until it joins a campaign or a long run.

## 8. Coverage loop

In develop, the agent fuzzes a harness in short rounds and measures branch coverage of the source after each one. The engine's own counter steers mutation but doesn't say which code was reached. Each step of the loop answers one question:
- **When to act: at a plateau.** Rounds are short, because the hours go into long runs. Two rounds in a row that add nothing make a plateau, since one stalled round is weak evidence and a diagnosis costs far more than a few CPU-minutes of fuzzing. A plateau starts a diagnosis; it doesn't mean the code is fully explored.
- **Where to look: blocked branches and sinks.** The diagnosis starts from branches never taken where another branch at the same point was: inputs already reach these checks, so they're the cheapest gaps to open. It also checks the target's sinks (below).
- **Which action: the one that unlocks the most security-relevant code; cost only breaks ties.** The actions are seeds, a dictionary, targeted inputs (generated variants of inputs that already get close), a harness or build change, or a new target. They're ranked by how likely the code behind the gap is to hold a security bug (sinks, size arithmetic and past bugs first; ordinary parsing next; logging and debug code last), then by how much of it. A gap that resisted several actions waits for new evidence.
- **Whether to keep it: only if it worked.** A harness or build change stays if the code it aimed at becomes covered, or if coverage at the next plateau is clearly higher than before. Seeds and dictionary entries always stay.

Coverage shows that code ran, not that its dangerous values were tried, so sinks are tracked on their own, an idea from [GONDAR](https://arxiv.org/abs/2604.01645), a paper on sink-centric Java fuzzing:
- **Each group of sinks has a state, so effort goes where bugs can still be found:** not reached, reached, triggered, tested (inputs aimed at it ran with sanitizers and detectors on, without a finding) or sanitized (the code provably blocks the attack). Tested and sanitized groups get no more inputs until their code changes.
- **A sink that's reached but not triggered gets targeted inputs.** The condition that breaks it is stated, and a generator varies the inputs that reach it toward that condition. The variants run with sanitizers and detectors on, since coverage shows only that the sink was reached. In C/C++, memory-safety hotspots get the same treatment.
- **Regular expressions get their own check** in Java and JS. Those applied to input are screened for backtracking and confirmed through the harness, since a slow regular expression (ReDoS) rarely reaches the fuzzer's timeout at the small input sizes fuzzers use.

## 9. Seeds and corpora

A harness starts from seeds, inputs that already exercise its code, and its corpus grows from there:
- **Seeds come from what exists first, then from the format's spec:** the corpora of earlier campaigns and existing harnesses, then the project's own test data and fixtures, then small valid inputs written from the spec, one per message type or record kind. Real inputs reach deep code from the first round, while mutation alone rarely builds valid structure.
- **Dictionaries are made for each project:** its existing ones first, then the literals in its scope files and the format's keywords. None ship with the skills.
- **Targeted inputs (§8) start from corpus inputs that already reach the code next to the gap.** Minimizing the corpus keeps only inputs that add coverage, so it deletes most targeted inputs for a sink: they run the same code and differ only in the values that matter, such as lengths around a buffer's size. Their generators therefore re-create them before long runs.

Each harness keeps one corpus:
- **One corpus per harness,** whichever engine runs it. Develop rounds write into it; long runs write into their own directory, which is merged back at analysis.
- **Minimized when the harness's develop loop ends,** since its long runs start from that corpus, and at analysis merges, but not after every round. A few minutes of fuzzing can leave tens of thousands of files, which every replay then loads.
- **Crashing inputs stay in the corpus.** A bug that keeps getting in the way is fixed or filtered instead (§11). A minimizing merge may still drop a crashing input (libFuzzer's does, Jazzer's doesn't), so once the bug is fixed, its original input goes back into the corpus, since it now reaches code the fuzzer couldn't pass before.

## 10. Long runs and analysis

A long run fuzzes one harness for hours or days, with several copies (parallel fuzzer processes) on the cores the user gives it:
- **The agent prepares the command; the user starts it** (§3.1). The agent starts a run only when asked, warning that it may end with the agent's session, and never stops or restarts one the user started, unless they ask.
- **Each run uses a git worktree of its launch commit,** outside the checkout, so develop can keep changing the checkout while the run's builds stay as they were.
- **Copies are independent processes, with one launcher per harness,** so several harnesses can run at once on one machine.
- **Copies keep going past findings** where the engine allows it (AFL++, Jazzer), since a stopped copy leaves its cores idle; triage groups the duplicates.
- **A run that died isn't resumed:** its corpus already carries the progress.

Analysis, when the user asks, turns a run's results into evidence, fixes and next steps:
- **Evidence first, on the run's builds:** every finding is triaged on the build that found it, since a later build may no longer reproduce it. Fixes, reports, corpus merges and coverage follow on the current build.
- **Each harness is sorted by its result:** one whose coverage still grew runs longer next time, one that plateaued with gaps goes back to develop, and sinks reached but not triggered get targeted inputs.
- **A harness waits for its run's analysis.** Autonomous develop doesn't change a harness while it has a run going or not yet analyzed, so the next run starts from that run's inputs; in interactive mode, the user decides.

## 11. Triage and fixes

Triage makes each finding reproducible and understood before anything is fixed or reported:
- **Every crash file is reproduced on the build that found it.**
- **Group by the top stack frames in the project's code,** leaving out the harness's frames, so crashes of the same bug land in one bucket.
- **Minimize without slipping to another bug:** a minimized input stays only if it still crashes in the same bucket; otherwise the original is kept.
- **Every real bug gets a reproducer that doesn't need the harness:** a small program that calls the project's API with the values the harness passed, including the check that caught the bug. Reports, regression tests and later checks all use it.

A bug is handled right away only if it blocks fuzzing:
- **Blocking bugs,** which keep a harness from making progress (a crash on the seeds, or one that keeps ending runs early), are handled right away. Memory-safety bugs are fixed. Other bugs are filtered out in the harness and their fix is queued, since whether that code is wrong is often the maintainers' call.
- **Other bugs wait** until their target's develop loop ends, or for analysis.

A fix is a proposal for the maintainers, so it's kept small and checked:
- **Only bugs upstream hasn't fixed:** before any fix, the reproducer runs on upstream HEAD and the latest release. A bug fixed at HEAD but not yet released may be a silent security fix, and the user is told.
- **Minimal, at the root cause, in the project's style,** one commit each, and never a workaround that only hides the crash.
- **The regression test is the reproducer,** turned into a test in the project's framework, and it must fail before the fix.
- **Passing the tests isn't enough.** A fix must pass its reproducer, the project's tests and the gate, but many wrong patches do, so every fix also gets a review, and one the user hasn't reviewed is reported as unreviewed.

When the user asks only for triage ("triage these crashes"), the agent stops once the crashes are understood: grouped, each with a verdict and a reproducer. It fixes or reports only when asked.

## 12. Verdicts, reports and hand-off

Each real bug gets a verdict, such as `vulnerability`, `library_hardening` (a real bug, but on data the threat model trusts) or `harness_bug` (a bug in the harness). Two rules decide whether it's a `vulnerability`:
- **The crash type sets the starting point,** as ClusterFuzz classifies it: memory corruption is a security bug; null dereferences, stack overflows, running out of memory or time, and leaks aren't. A crash that only stops the program counts only in a server or shared service that parses untrusted input, and only if a small input triggers it.
- **An attacker must be able to trigger it:** the reproducer must also crash through the project's command line or public API, on a build without fuzzing-only patches such as a disabled checksum, since a harness can take shortcuts an attacker can't.

The verdict decides what's reported and what stays private:
- **Only a `vulnerability` gets a security report,** a draft GitHub Security Advisory with a CVSS score; other real bugs go into one ordinary report per project.
- **Anything that may reveal a vulnerability stays private until its fix is public:** harnesses, seeds, tests and fixes that reach a `vulnerability`, a crash of a security type, or a finding not yet understood. The crash type counts even when the verdict says otherwise, since that judgment can be wrong. So fixes never go in harness pull requests, and every summary lists the branches not to push.

Harnesses go upstream so the project keeps fuzzing:
- **To the project's repository, or to its OSS-Fuzz folder** if the project keeps its harnesses there. Java and JS harnesses in the repository also run as regression tests in its CI. Each goes on a branch from upstream HEAD with only harness and build changes.
- **An optional CI job** replays the corpus on each pull request and blocks only on that, since a finding in its short fuzz run may be an old bug the pull request didn't cause.
