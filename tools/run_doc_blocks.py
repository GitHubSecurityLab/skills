#!/usr/bin/env python3
"""Run the skills' canonical engine commands, taken from the docs, on toy projects.

The language skills mark their canonical command blocks with an HTML comment
right before the fenced block:

    <!-- ci: c-develop -->
    ```sh
    $L start --id r<round>-$H ...
    ```

Placeholders are `<snake_case>` names; each scenario fills them, and a
placeholder left unfilled is an error. Blocks marked as environment blocks
(shell assignments) are prepended to the blocks run after them in the same
scenario. JSON blocks are written to a file after filling.

Each scenario copies a toy project from tests/toys/ into a fresh git
repository, sets up the workspace with init.py as the skills do, and runs
its blocks in the order listed below, with a little glue (builds, seeds)
and checks of the outcome.

    python3 tools/run_doc_blocks.py check            # markers and scenarios agree
    python3 tools/run_doc_blocks.py list
    python3 tools/run_doc_blocks.py run c-libfuzzer [--keep]

A scenario runs in a new temporary directory, or in
$DOCBLOCKS_WORKDIR/<scenario> when that variable is set (emptied first).
CI sets it so that build paths are the same on every run and ccache hits:
coverage builds put the compiler's working directory into ccache's key.
"""

import argparse
import glob
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
TOYS = os.path.join(ROOT, "tests", "toys")
SCRIPTS = os.path.join(SKILLS, "fuzzing", "fuzz-campaign", "scripts")

MARKER = re.compile(r"^\s*<!-- ci: ([a-z0-9][a-z0-9-]*) -->\s*$")
FENCE = re.compile(r"^(\s*)```(sh|json|javascript|java|cpp)\s*$")
SKIP_MARKER = re.compile(r"^\s*<!-- ci-skip:(.*?)-->\s*$")
LANGUAGE_SKILLS = ("fuzz-c-cpp", "fuzz-java", "fuzz-js")
PLACEHOLDER = re.compile(r"<([a-z][a-z0-9_]*)>")


class DocError(Exception):
    pass


class Block:
    def __init__(self, name: str, kind: str, text: str, path: str, line: int) -> None:
        self.name = name
        self.kind = kind
        self.text = text
        self.path = path
        self.line = line

    def where(self) -> str:
        return "%s:%d" % (os.path.relpath(self.path, ROOT), self.line)

    def placeholders(self) -> set:
        return set(PLACEHOLDER.findall(self.text))


def extract_blocks(paths: list) -> dict:
    blocks: dict = {}
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        index = 0
        while index < len(lines):
            match = MARKER.match(lines[index])
            if not match:
                index += 1
                continue
            name = match.group(1)
            where = "%s:%d" % (os.path.relpath(path, ROOT), index + 1)
            fence = FENCE.match(lines[index + 1]) if index + 1 < len(lines) else None
            if not fence:
                raise DocError("%s: marker %r isn't followed by a ```sh, ```json, ```javascript, ```java or ```cpp block" % (where, name))
            end = index + 2
            while end < len(lines) and lines[end].strip() != "```":
                end += 1
            if end == len(lines):
                raise DocError("%s: block %r isn't closed" % (where, name))
            if name in blocks:
                raise DocError("%s: block %r is also marked at %s" % (where, name, blocks[name].where()))
            indent = len(fence.group(1))
            body = [line[indent:] if line[:indent].strip() == "" else line for line in lines[index + 2 : end]]
            blocks[name] = Block(name, fence.group(2), "\n".join(body) + "\n", path, index + 1)
            index = end + 1
    return blocks


def doc_paths(skills: str = SKILLS) -> list:
    return sorted(glob.glob(os.path.join(skills, "**", "*.md"), recursive=True))


def language_doc_paths(skills: str = SKILLS) -> list:
    """The language skills' docs, by their directories under skills/: the checkout's own path may contain a
    language skill's name (a worktree called fuzz-js)."""
    return [p for p in doc_paths(skills)
            if set(os.path.dirname(os.path.relpath(p, skills)).split(os.sep)) & set(LANGUAGE_SKILLS)]


def unmarked_blocks(paths: list) -> list:
    """Every ```sh block of a language skill is a canonical command: it has a `ci:` marker, or
    `ci-skip: <reason>` when no scenario runs it, so untested commands are visible."""
    errors = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        in_fence = False
        for index, line in enumerate(lines):
            if not line.strip().startswith("```"):
                continue
            in_fence = not in_fence
            if not in_fence or not re.match(r"^\s*```sh\s*$", line):
                continue
            previous = lines[index - 1] if index else ""
            skip = SKIP_MARKER.match(previous)
            if skip and not skip.group(1).strip():
                errors.append("%s:%d: ci-skip marker without a reason" % (os.path.relpath(path, ROOT), index))
            elif not skip and not MARKER.match(previous):
                errors.append("%s:%d: ```sh block without a ci: or ci-skip: <reason> marker"
                              % (os.path.relpath(path, ROOT), index + 1))
    return errors


def fill(block: Block, values: dict) -> str:
    missing = sorted(block.placeholders() - set(values))
    if missing:
        raise DocError("%s: block %r has no value for %s" % (block.where(), block.name, ", ".join("<%s>" % m for m in missing)))
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), block.text)


# --------------------------------------------------------------------------
# Running a scenario


class Scenario:
    """One end-to-end path: a toy project and the blocks it runs, in order."""

    def __init__(self, name: str, toy: str, uses: list, func, systems=("Linux", "Darwin")) -> None:
        self.name = name
        self.toy = toy
        self.uses = uses
        self.func = func
        self.systems = systems


class UsedBlocks(dict):
    """A scenario's blocks; reading one (to run it, or for its text) marks it used."""

    def __init__(self, blocks: dict) -> None:
        super().__init__(blocks)
        self.used: set = set()

    def __getitem__(self, name: str) -> Block:
        self.used.add(name)
        return super().__getitem__(name)


class Context:
    def __init__(self, blocks: dict, scenario: Scenario, work: str, verbose: bool) -> None:
        self.blocks = UsedBlocks(blocks)
        self.scenario = scenario
        self.repo = os.path.join(work, "repo")
        self.F = os.path.join(self.repo, ".seclab", "fuzz")
        self.env = dict(os.environ)
        self.env.update({"F": self.F, "L": "python3 %s" % os.path.join(self.F, "bin", "launcher.py")})
        self.prelude = []
        self.verbose = verbose
        shutil.copytree(os.path.join(TOYS, scenario.toy), self.repo)
        self.sh("git init -q && git add -A && git -c user.name=ci -c user.email=ci@example.com commit -qm toy")
        self.sh('python3 "%s" --branch current --notice-shown > /dev/null' % os.path.join(SCRIPTS, "init.py"))
        for sub in ("corpus", "dict", "coverage", "crashes", "runs"):
            os.makedirs(os.path.join(self.F, sub), exist_ok=True)

    def say(self, text: str) -> None:
        print(text, flush=True)

    def set(self, **values) -> None:
        self.env.update({k: str(v) for k, v in values.items()})

    def sh(self, command: str, check: bool = True) -> subprocess.CompletedProcess:
        if self.verbose:
            self.say("$ " + command.strip())
        result = subprocess.run(
            ["bash", "-o", "pipefail", "-c", command], cwd=self.repo, env=self.env, text=True, capture_output=True
        )
        if self.verbose or (check and result.returncode != 0):
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
        if check and result.returncode != 0:
            raise DocError("command failed with exit status %d:\n%s" % (result.returncode, command))
        return result

    def block(self, name: str, ok=(0,), subdir: str = "", add=None, replace=(), **values) -> subprocess.CompletedProcess:
        """Run a marked sh block (after the environment blocks set so far), in the repository or a subdirectory.
        add=(anchor, text) inserts text before the anchor, and replace=((old, new), ...) edits the block, as the
        prose around a block tells (an extra flag; C++ instead of C)."""
        if name not in self.scenario.uses:
            raise DocError("scenario %s runs block %r without listing it in uses" % (self.scenario.name, name))
        block = self.blocks[name]
        edits = ([(add[0], add[1] + add[0])] if add else []) + list(replace)
        for old, new in edits:
            if old not in block.text:
                raise DocError("%s: block %r has no %r to replace with %r" % (block.where(), name, old, new))
            block = Block(block.name, block.kind, block.text.replace(old, new, 1), block.path, block.line)
        text = fill(block, values)
        self.say("== %s (%s)" % (name, block.where()))
        cd = "cd %s\n" % shlex.quote(subdir) if subdir else ""
        result = self.sh("set -e\n" + "".join(self.prelude) + cd + text, check=False)
        if result.returncode not in ok:
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            raise DocError("block %s (%s) exited %d" % (name, block.where(), result.returncode))
        return result

    def env_block(self, name: str, **values) -> None:
        """An environment block: added to .seclab/fuzz/env.sh, which every later block sources, as an
        agent's commands do (they don't keep shell variables)."""
        if name not in self.scenario.uses:
            raise DocError("scenario %s uses block %r without listing it in uses" % (self.scenario.name, name))
        block = self.blocks[name]
        self.say("== %s (%s, environment)" % (name, block.where()))
        with open(os.path.join(self.F, "env.sh"), "a") as handle:
            handle.write(fill(block, values))
        self.prelude = [". .seclab/fuzz/env.sh\n"]

    def write_block(self, name: str, path: str, **values) -> str:
        if name not in self.scenario.uses:
            raise DocError("scenario %s uses block %r without listing it in uses" % (self.scenario.name, name))
        block = self.blocks[name]
        target = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as handle:
            handle.write(fill(block, values))
        if block.kind == "json":
            with open(target) as handle:
                json.load(handle)
        self.say("== %s (%s) -> %s" % (name, block.where(), path))
        return target

    # Checks

    def meta(self, run_id: str) -> dict:
        with open(os.path.join(self.F, "runs", run_id, "meta.json")) as handle:
            return json.load(handle)

    def findings(self, run_id: str) -> list:
        """Absolute paths of the run's new findings, from meta.json."""
        rdir = os.path.join(self.F, "runs", run_id)
        return [os.path.join(rdir, f["path"]) for c in self.meta(run_id)["copies"] for f in c.get("new_findings", [])]

    def expect(self, condition: bool, message: str) -> None:
        if not condition:
            raise DocError("check failed: " + message)
        self.say("   ok: " + message)

    def expect_state(self, run_id: str, *states) -> dict:
        meta = self.meta(run_id)
        got = [c["state"] for c in meta["copies"]]
        self.expect(all(s in states for s in got), "%s copies are %s (%s)" % (run_id, " or ".join(states), ", ".join(got)))
        return meta

    def log(self, run_id: str, copy: int = 0) -> str:
        with open(os.path.join(self.F, "runs", run_id, "copy-%d" % copy, "log"), errors="replace") as handle:
            return handle.read()

    def expect_covered(self, lcov: str, path: str) -> None:
        """The lcov file has data for `path` (from the repository root), read through a scope file."""
        scope = os.path.join(self.F, "targets", "ci.scope")
        os.makedirs(os.path.dirname(scope), exist_ok=True)
        with open(scope, "w") as handle:
            handle.write(path + "\n")
        out = self.sh('python3 "$F/bin/coverage.py" summary "%s" --root . --scope "%s"' % (lcov, scope)).stdout
        lines = re.search(r"\*\*Lines:\*\* (\d+)/(\d+)", out)
        self.expect(
            path in out and "incomplete" not in out and lines is not None and int(lines.group(1)) > 0,
            "the lcov covers %s (%s lines)" % (path, lines.group(0) if lines else "no"),
        )

    def files(self, directory: str) -> list:
        path = os.path.join(self.repo, directory)
        return sorted(os.listdir(path)) if os.path.isdir(path) else []


# --------------------------------------------------------------------------
# Scenarios. Each lists the blocks it uses; `check` verifies every marked
# block is used by a scenario and every used block is marked, and a run
# fails if it didn't run (or read) every block it lists.

SCENARIOS: dict = {}


def scenario(name: str, toy: str, uses: list, systems=("Linux", "Darwin")):
    def register(func):
        SCENARIOS[name] = Scenario(name, toy, uses, func, systems)
        return func

    return register


def system() -> str:
    return os.uname().sysname


def documented(skill: str, ref: str, pattern: str) -> str:
    """A value a skill's prose documents (group 1 of the pattern), which the scenario then runs."""
    with open(os.path.join(SKILLS, "fuzzing", skill, "references", ref), encoding="utf-8") as handle:
        match = re.search(pattern, " ".join(handle.read().split()))
    if not match:
        raise DocError("%s's %s documents nothing matching %r" % (skill, ref, pattern))
    return match.group(1)


def expect_repeat(ctx: Context, run_id: str, flag: str) -> None:
    """The in-process repeat (fuzz-triage, step 2) ran the input as often as the flag says, in one process."""
    ctx.expect_state(run_id, "finished")
    runs = re.search(r"Running 1 inputs (\d+) time\(s\) each", ctx.log(run_id))
    ctx.expect(runs is not None and "-runs=" + runs.group(1) == flag,
               "%s runs the input %s times in one process" % (flag, runs.group(1) if runs else "no"))


def llvm_bin() -> str:
    """LLVM_BIN, or Homebrew's LLVM on macOS, or llvm-config's bindir."""
    if os.environ.get("LLVM_BIN"):
        return os.environ["LLVM_BIN"]
    if system() == "Darwin":
        return "/opt/homebrew/opt/llvm/bin"
    for config in ["llvm-config"] + ["llvm-config-%d" % v for v in range(30, 13, -1)]:
        if shutil.which(config):
            return subprocess.run([config, "--bindir"], capture_output=True, text=True).stdout.strip()
    raise DocError("no LLVM found; set LLVM_BIN")


def variant_flags(variant: str) -> tuple:
    """(compile flags, link flags) of a variant in fuzz-c-cpp's build.md Variants table: the first code
    span of each column, with $UB and $HARDEN unexpanded."""
    with open(os.path.join(SKILLS, "fuzzing", "fuzz-c-cpp", "references", "build.md")) as handle:
        row = re.search(r"^\| `%s` \|(.*)$" % re.escape(variant), handle.read(), re.M)
    if not row:
        raise DocError("build.md's Variants table has no row for %s" % variant)
    cells = row.group(1).split(" | ")
    return tuple(re.search(r"`([^`]*)`", cell).group(1) for cell in cells[:2])


def ubsan(ctx: Context) -> str:
    """The one UBSan list: build.md's c-ub block."""
    if "c-ub" not in ctx.scenario.uses:
        raise DocError("scenario %s uses the UBSan list without listing c-ub" % ctx.scenario.name)
    return re.search(r"^UB=(\S+)$", ctx.blocks["c-ub"].text, re.M).group(1)


def harden(ctx: Context) -> str:
    """The C++ standard-library checks: build.md's c-ub block."""
    ubsan(ctx)
    return re.search(r'^HARDEN="([^"]*)"$', ctx.blocks["c-ub"].text, re.M).group(1)


def c_flags(ctx: Context, variant: str) -> tuple:
    return tuple(f.replace("$UB", ubsan(ctx)).replace("$HARDEN", harden(ctx)) for f in variant_flags(variant))


def build_c_toy(ctx: Context, variants=("libfuzzer", "coverage"), harness="fuzz_record") -> None:
    clang = os.path.join(llvm_bin(), "clang")
    for variant in variants:
        cflags, ldflags = c_flags(ctx, variant)
        out = "$F/build/%s" % variant
        # C: -fno-sanitize=function last, also after the link flags of a one-step compile and link.
        ctx.sh(
            'mkdir -p "{out}" && "{cc}" {cf} -fno-sanitize=function -c src/record.c -o "{out}/record.o" && '
            '"{cc}" {cf} {lf} -fno-sanitize=function fuzz/{h}.c "{out}/record.o" -o "{out}/{h}"'.format(
                out=out, cc=clang, cf=cflags, lf=ldflags, h=harness
            )
        )


@scenario(
    "c-libfuzzer",
    "c",
    ["c-env", "c-ub", "c-develop", "c-repro", "c-minimize", "triage-rebucket", "c-reproducer", "c-long", "c-cov-replay", "c-cov-export",
     "handoff-held-back", "c-merge", "held-back-branch", "build-record", "build-stale", "c-leak-check", "c-reach",
     "fresh-worktrees", "dict-literals", "base-worktree", "release-rebase", "gate-tree-before", "gate-tree-after",
     "gate-progress", "c-call-sequence"],
)
def c_libfuzzer(ctx: Context) -> None:
    ctx.set(H="record")
    # The gate's stray-file check: nothing for an unchanged tree; new content in files that were
    # already changed or untracked before the gate, and new files. It writes no objects into .git,
    # which Codex's sandbox makes read-only.
    objects = lambda: ctx.sh("find .git/objects -type f | sort").stdout
    before = objects()
    ctx.sh("echo '/* local */' >> src/record.h && echo notes > notes.txt")
    ctx.block("gate-tree-before", h="record", n=1)
    out = ctx.block("gate-tree-after", h="record", n=1, ok=(0, 1)).stdout
    ctx.expect(out.strip() == "", "the stray-file check reports nothing for an unchanged tree")
    ctx.block("gate-tree-before", h="record", n=2)
    ctx.sh("echo '/* more */' >> src/record.h && echo more >> notes.txt && echo new > fuzz/new.txt")
    out = ctx.block("gate-tree-after", h="record", n=2, ok=(0, 1)).stdout
    ctx.expect(all(p in out for p in ("src/record.h", "notes.txt", "fuzz/new.txt")),
               "the stray-file check sees new content in already changed and untracked files (%s)" % out.strip())
    ctx.expect(objects() == before, "the stray-file check adds nothing to .git/objects")
    left = [os.path.join("gate-record-%d" % n, name) for n in (1, 2) for name in ("objects", "tree.index")
            if os.path.exists(os.path.join(ctx.F, "runs", "gate-record-%d" % n, name))]
    ctx.expect(not left, "the stray-file check deletes its snapshot's objects and index (left: %s)" % left)
    ctx.sh("git checkout -q -- src/record.h && rm notes.txt fuzz/new.txt")
    # Freshness (fuzz-triage's report.md): a later report moves the same worktrees to the new commits.
    # The clone is a fork's: origin is the fork, which doesn't follow the project (upstream).
    up = os.path.join(os.path.dirname(ctx.repo), "upstream")
    fork = os.path.join(os.path.dirname(ctx.repo), "fork")
    ctx.sh('git clone -q . "{0}" && git -C "{0}" tag v1.0 && git clone -q "{0}" "{1}" && git remote add origin "{1}" '
           '&& git fetch -q origin && git remote set-head origin -a && git remote add upstream "{0}"'.format(up, fork))
    fresh = dict(checkout=ctx.repo, repo=os.path.basename(ctx.repo))
    ctx.block("fresh-worktrees", release_tag="v1.0", **fresh)
    ctx.sh('git -C "{0}" -c user.name=ci -c user.email=ci@example.com commit -q --allow-empty -m next '
           '&& git -C "{0}" tag v1.1'.format(up))
    ctx.block("fresh-worktrees", release_tag="v1.1", **fresh)
    trees = os.path.join(os.path.dirname(ctx.repo), os.path.basename(ctx.repo) + ".seclab-worktrees")
    upstream = ctx.sh('git -C "%s" rev-parse HEAD' % up).stdout.strip()
    ctx.expect(all(ctx.sh('git -C "%s/%s" rev-parse HEAD' % (trees, t)).stdout.strip() == upstream
                   for t in ("fresh-head", "fresh-release")),
               "a second report moves the freshness worktrees to upstream's new HEAD and release")
    ctx.env_block("c-env", detect_leaks=1 if system() == "Linux" else 0, llvm_bin=llvm_bin())
    ctx.env_block("c-ub")
    build_c_toy(ctx)
    for variant in ("libfuzzer", "coverage"):
        ctx.block("build-record", variant=variant)
    ctx.expect("stale" not in ctx.block("build-stale").stdout, "fresh builds aren't stale")
    ctx.sh('cp seeds/ok "$F/corpus/$H/" 2>/dev/null || { mkdir -p "$F/corpus/$H" && cp seeds/ok "$F/corpus/$H/"; }')
    # The dictionary from the scope's literals, in every quote style; the develop round's -dict parses it.
    fixture = os.path.join(os.path.dirname(ctx.repo), "dict-fixture.js")
    with open(fixture, "w", encoding="utf-8") as handle:
        handle.write("import x from './mod';\nconst y = require('lodash');\nexport { z } from \"./zz\";\n"
                     "const a = 'name', b = `tmpl`, c = \"q\\\"t\", d = 'it\\'s', g = 'sa\"y';\n"
                     "log(\"invalid header %d\", n); const e = \"caf\u00e9\", f = \"a\\nb\";\n"
                     "const h = \"\\x0\", i = '\\xzz', j = \"\\x41\\x42\", k = 'a\\\\b';\n")
    ctx.block("dict-literals", harness="record", scope_files="src/record.c " + shlex.quote(fixture))
    with open(os.path.join(ctx.F, "dict", "record.dict"), encoding="utf-8") as handle:
        entries = [line for line in handle.read().splitlines() if not line.startswith("#")]
    ctx.expect(sorted(entries) == sorted(['"REC1"', '"name"', '"q\\"t"', '"tmpl"', '"\\x41\\x42"', '"a\\\\b"']),
               "the dictionary has the literals in every quote style, without includes, module paths, "
               "messages, format strings, non-ASCII or escapes libFuzzer rejects (%s)" % " ".join(entries))
    ctx.block("dict-literals", harness="none", scope_files="src/record.h")
    ctx.say("   ok: scope files without literals add none, and the pipeline succeeds")
    common = dict(binary="fuzz_record", memory_mb=2048, input_timeout_s=10, max_len=4096)
    # The gate's seeds-only snapshot (gate.md, step 0), which check 2 compares after fuzzing.
    ctx.block("c-cov-replay", n=0, timeout_s=120, **common)
    ctx.block("c-cov-export", n=0, round=0, binary="fuzz_record")

    # Develop round: finds the planted overflow.
    ctx.block("c-develop", round=1, run_s=120, **common)
    ctx.expect_state("r1-record", "found-crash")
    ctx.expect("-max_len is not provided" not in ctx.log("r1-record"), "the develop run takes the harness's input cap")
    finding = ctx.findings("r1-record")[0]

    # Triage: reproduce, bucket, minimize.
    # ASan exits 1 on Linux; on macOS abort_on_error defaults to 1 (SIGABRT, 134).
    ctx.block("c-repro", n=1, timeout_s=60, finding=finding, ok=(1, 134), **common)
    ctx.expect("AddressSanitizer" in ctx.log("repro-record-1"), "the reproduction prints the ASan report")
    buckets = ctx.sh('python3 "$F/bin/crash_buckets.py" "$F"/runs/repro-*/copy-*/log --root .').stdout
    ctx.expect("parse_record" in buckets and "repro-record-1" in buckets, "crash_buckets.py names the frame and the run")
    ctx.block("c-minimize", n=1, timeout_s=120, bucket="overflow", finding=finding, **common)
    minimized = os.path.join(ctx.F, "crashes", "overflow", "minimized")
    ctx.block("c-repro", n=2, timeout_s=60, finding=minimized, ok=(1, 134), **common)
    ctx.expect("AddressSanitizer" in ctx.log("repro-record-2"), "the minimized input reproduces")
    out = ctx.block("triage-rebucket", h="record", original=1, minimized=2).stdout
    ctx.expect("2 logs in 1 bucket" in out and "no crash detected" not in out,
               "the minimized input stays in the original's bucket")
    # Before State non-reproducible: the documented in-process repeat, here on an input that doesn't crash.
    repeat = documented("fuzz-c-cpp", "triage.md", r"`(-runs=\d+)` before the file")
    ctx.block("c-repro", n=3, timeout_s=60, finding=os.path.join(ctx.repo, "seeds", "ok"), add=('"<finding>"', repeat + " "), **common)
    expect_repeat(ctx, "repro-record-3", repeat)

    # The bug's reproducer (written by the agent; here from the toy) fails before the fix.
    ctx.sh('cp repro.c "$F/crashes/overflow/repro.c"')
    reproducer = dict(bucket="overflow", include_dir=os.path.join(ctx.repo, "src"),
                      tree_build=os.path.join(ctx.F, "build"), library="record.o", timeout_s=120, memory_mb=2048,
                      repro_leaks=1)
    ctx.block("c-reproducer", n=1, ok=(1, 134), **reproducer)
    ctx.expect("Assertion" in ctx.log("poc-record-1"), "the reproducer fails with the assertion before the fix")
    # An out-of-memory bug's reproducer: an allocation over memory_mb aborts, as in the fuzzer, though the code checks for NULL.
    os.makedirs(os.path.join(ctx.F, "crashes", "oom"), exist_ok=True)
    with open(os.path.join(ctx.F, "crashes", "oom", "repro.c"), "w") as handle:
        handle.write('#include <stdio.h>\n#include <stdlib.h>\nvoid *volatile kept;\n'
                     'int main(void) { kept = malloc((size_t)3 << 30); puts(kept ? "allocated" : "NULL"); return 0; }\n')
    ctx.block("c-reproducer", n=3, ok=(1, 134), **dict(reproducer, bucket="oom"))
    ctx.expect("allocation-size-too-big" in ctx.log("poc-record-3"), "the reproducer fails on an allocation over the memory limit")
    # A leak's reproducer fails on macOS too, where c-env turns leak detection off for libFuzzer's RSS thread.
    os.makedirs(os.path.join(ctx.F, "crashes", "leak"), exist_ok=True)
    with open(os.path.join(ctx.F, "crashes", "leak", "repro.c"), "w") as handle:
        handle.write('#include <stdlib.h>\nvoid *volatile kept;\nint main(void) { kept = malloc(64); kept = 0; return 0; }\n')
    ctx.block("c-reproducer", n=4, ok=(1, 23, 134), **dict(reproducer, bucket="leak"))
    ctx.expect("LeakSanitizer: detected memory leaks" in ctx.log("poc-record-4"), "a leak's reproducer fails with the leak report")
    # A C++ reproducer (clang++, without -fno-sanitize=function) keeps the standard library's checks: an index
    # past a vector's size but within its capacity (fuzz/fuzz_vector.cc), which ASan misses with libstdc++.
    os.makedirs(os.path.join(ctx.F, "crashes", "vector"), exist_ok=True)
    with open(os.path.join(ctx.F, "crashes", "vector", "repro.cc"), "w") as handle:
        handle.write("#include <vector>\nint main() { std::vector<int> v; v.reserve(8); v.push_back(1); return v[3] == 42; }\n")
    cxx = (("clang ", "clang++ "), (" -fno-sanitize=function", ""), ("repro.c", "repro.cc"))
    ctx.block("c-reproducer", n=5, ok=(1, 134), replace=cxx, **dict(reproducer, bucket="vector"))
    ctx.expect(re.search("assertion", ctx.log("poc-record-5"), re.I),
               "a C++ reproducer fails with the standard library's bounds check")

    # Long run: three copies, prepared, then started from the spec.
    ctx.block("c-long", n=1, copies=3, run_s=20, tree=ctx.repo, tree_build=os.path.join(ctx.F, "build"), **common)
    with open(os.path.join(ctx.F, "runs", "record-long1", "spec.json")) as handle:
        copies = json.load(handle)["copies"]
    ctx.expect(["-use_value_profile=1" in c["command"] for c in copies] == [False, False, True],
               "every third copy runs with value profile")
    ctx.sh('cp "$F/dict/$H.dict" "$F/runs/$H-long1/$H.dict"')
    ctx.sh('$L start --skip-cpu-check --spec "$F/runs/$H-long1/spec.json"')
    ctx.expect_state("record-long1", "found-crash", "finished")

    # A crashing input in the corpus (e.g. an outside seed): the replay continues past it.
    ctx.sh('cp "%s" "$F/corpus/$H/"' % finding)
    ctx.block("c-cov-replay", n=1, timeout_s=120, **common)
    ctx.expect_state("cov-record-1", "finished")
    ctx.expect(len(ctx.findings("cov-record-1")) == 1, "the replay records the crashing input as a finding")
    ctx.block("c-cov-export", n=1, round=1, binary="fuzz_record")
    ctx.expect_covered("$F/coverage/$H/r1.lcov", "src/record.c")
    # The gate's progress check: fuzzing covers scope branches the seeds didn't; no growth shows as +0.
    ctx.sh('mkdir -p "$F/targets" && echo src/record.c > "$F/targets/record.scope"')
    grown = lambda out: re.search(r"^- \*\*Branches:\*\*.*\(([+-]\d+) covered\)", out, re.M)
    out = ctx.block("gate-progress", h="record", id="record", round=0, next=1).stdout
    ctx.expect(grown(out) and int(grown(out).group(1)) > 0, "fuzzing grows the scope's branches over the seeds' (%s)"
               % (grown(out).group(0) if grown(out) else "no Branches line"))
    out = ctx.block("gate-progress", h="record", id="record", round=0, next=0).stdout
    ctx.expect(grown(out) and grown(out).group(1) == "+0", "a snapshot without growth shows +0 covered")
    # Hand-off: a harness whose last lcov reaches a held-back bug's root-cause line is held back.
    ctx.sh('mkdir -p "$F/handoff" && printf "id\\tfile\\tline\\noverflow\\tsrc/record.c\\t16\\n" > "$F/handoff/held-back.tsv"')
    out = ctx.block("handoff-held-back", h="record", last="r1").stdout
    ctx.expect("| overflow | - | src/record.c:16 | reached |" in out, "the hand-off check finds the harness reaching the held-back line")

    # W1: the merge finds the bug, so nothing is swapped; after the fix, the
    # same merge from the untouched inputs finds nothing, and its result is swapped in.
    ctx.block("c-merge", n=1, timeout_s=120, long_run="record-long1", **common)
    ctx.expect(len(ctx.findings("merge-record-1")) >= 1, "the first merge reports the crashing input")
    # The overflow is held back: its fix goes on its own branch, where the campaign continues.
    started_on = ctx.sh("git branch --show-current").stdout.strip()
    ctx.block("held-back-branch", bucket="overflow")
    ctx.sh("git apply fix.patch && git -c user.name=ci -c user.email=ci@example.com commit -qam 'fuzz(fix): overflow'")
    ctx.expect(ctx.sh("git branch --show-current").stdout.strip() == "seclab/held-back/overflow"
               and ctx.sh("git diff --quiet %s HEAD -- src/" % started_on, check=False).returncode == 1
               and ctx.sh("git status --porcelain --untracked-files=no").stdout == "",
               "the held-back fix is committed on its own branch, not on %s" % started_on)
    stale = ctx.block("build-stale").stdout
    ctx.expect("build/libfuzzer/" in stale and "build/coverage/" in stale, "builds from before the fix are stale")
    build_c_toy(ctx, ("libfuzzer",))
    ctx.block("build-record", variant="libfuzzer")
    stale = ctx.block("build-stale").stdout
    ctx.expect("build/libfuzzer/" not in stale and "build/coverage/" in stale, "a rebuilt variant is no longer stale")
    ctx.block("c-reproducer", n=2, **reproducer)
    ctx.expect_state("poc-record-2", "finished")
    ctx.block("c-merge", n=2, timeout_s=120, long_run="record-long1", **common)
    ctx.expect(ctx.findings("merge-record-2") == [], "the merge on the fixed build finds nothing")
    crash_name = os.path.basename(finding)[len("crash-"):]
    merged = ctx.files(".seclab/fuzz/runs/merge-record-2/corpus")
    ctx.expect(crash_name in merged, "the fixed bug's input adds coverage and is kept")
    ctx.sh('rm -rf "$F/corpus/$H" && mv "$F/runs/merge-$H-2/corpus" "$F/corpus/$H"')

    # reach.py: the smallest inputs that reach the copy in parse_record. The coverage build is
    # from before the fix: the kept input crashes in the replays, which go on and exit 0.
    out = ctx.block("c-reach", file="src/record.c", line=16, timeout_s=120, input_timeout_s=10,
                    binary="fuzz_record", memory_mb=2048).stdout
    ctx.expect("reached: " in out, "reach.py finds an input that reaches src/record.c:16")
    ctx.expect("finding: " in out and ctx.findings("reach-record-1-1"),
               "reach.py keeps and reports the input that crashes in its replays")

    # The gate's leak check: clean for this harness, a difference for a leaky one.
    build_c_toy(ctx, ("coverage",))
    out = ctx.block("c-leak-check", n=2, sample_size=8, timeout_s=60, input_timeout_s=10, binary="fuzz_record",
                    memory_mb=2048).stdout
    ctx.expect(clean_leak_check(out), "the leak check finds nothing in a clean harness")
    build_c_toy(ctx, ("coverage",), harness="fuzz_leaky")
    ctx.sh('mkdir -p "$F/corpus/leaky" && cp "$F/corpus/$H"/* "$F/corpus/leaky/"')
    ctx.set(H="leaky")
    out = ctx.block("c-leak-check", n=1, sample_size=8, timeout_s=60, input_timeout_s=10, binary="fuzz_leaky",
                    memory_mb=2048).stdout
    ctx.expect("### Different hit counts" in out and "fuzz_leaky.c" in out, "the leak check points at the leaking harness")
    # State that repeats work leaves the covered sets equal: only the hit counts show it. The corpus has
    # inputs of the same name in a subdirectory: the sample keeps them all.
    build_c_toy(ctx, ("coverage",), harness="fuzz_repeat")
    ctx.sh('mkdir -p "$F/corpus/repeat/dup" && cp "$F/corpus/leaky"/* "$F/corpus/repeat/" && cp "$F/corpus/leaky"/* "$F/corpus/repeat/dup/"')
    ctx.set(H="repeat")
    inputs = int(ctx.sh('find "$F/corpus/$H" -type f -size +0c | wc -l').stdout)
    out = ctx.block("c-leak-check", n=1, sample_size=inputs, timeout_s=60, input_timeout_s=10, binary="fuzz_repeat",
                    memory_mb=2048).stdout
    ctx.expect(len(ctx.files(".seclab/fuzz/runs/gate-repeat-1/sample")) == inputs,
               "the sample keeps inputs of the same name from different directories (%d)" % inputs)
    ctx.expect("### Different hit counts" in out and "src/record.c" in out, "the leak check sees work repeated across inputs")
    # C++: an index past a vector's size but within its capacity, which ASan misses with libstdc++.
    cflags, ldflags = c_flags(ctx, "libfuzzer")
    ctx.sh('"%s" %s %s fuzz/fuzz_vector.cc -o "$F/build/libfuzzer/fuzz_vector"'
           % (os.path.join(llvm_bin(), "clang++"), cflags, ldflags))
    vector = ctx.sh('"$F/build/libfuzzer/fuzz_vector" -runs=1', check=False)
    ctx.expect(vector.returncode != 0 and ("Assertion" in vector.stderr or "assertion" in vector.stderr),
               "the standard library's own check stops the overrun")
    sets = ctx.sh('python3 "$F/bin/coverage.py" overlap "$F/coverage/$H/gate-one.lcov" "$F/coverage/$H/gate-fresh.lcov" --root .').stdout
    ctx.expect("cover exactly the same" in sets, "a comparison of covered sets misses it")
    # The call-sequence idiom (harness.md) on a stand-in API: clean; without its handle table's
    # bookkeeping, ASan reports the harness's own use-after-free.
    call_sequence(ctx, cflags, ldflags)

    # The base worktree, and a new release: the campaign's commits without its fuzz(fix) commits.
    ctx.set(GIT_AUTHOR_NAME="ci", GIT_AUTHOR_EMAIL="ci@example.com", GIT_COMMITTER_NAME="ci",
            GIT_COMMITTER_EMAIL="ci@example.com")
    toy = ctx.sh("git rev-list --max-parents=0 HEAD").stdout.strip()
    base = os.path.join(trees, "base")
    ctx.block("base-worktree", checkout=ctx.repo, repo=os.path.basename(ctx.repo), base_commit=toy)
    ctx.expect(ctx.sh('git -C "%s" rev-parse HEAD' % base).stdout.strip() == toy,
               "with only a fix since the base commit, the base worktree is the base commit")
    ctx.sh('git worktree remove "%s"' % base)
    ctx.sh('echo notes > fuzz/NOTES && git add fuzz/NOTES && git commit -qm "fuzz(harness): notes"')
    ctx.block("base-worktree", checkout=ctx.repo, repo=os.path.basename(ctx.repo), base_commit=toy)
    ctx.expect(os.path.exists(os.path.join(base, "fuzz", "NOTES"))
               and ctx.sh('git -C "%s" diff --quiet %s HEAD -- src/' % (base, toy), check=False).returncode == 0,
               "the base worktree has the harness commits but not the fix")
    # The campaign is on its held-back branch: the new campaign gets a new branch, and that one keeps its fix.
    held_back = ctx.sh("git branch --show-current").stdout.strip()
    held_tip = ctx.sh("git rev-parse HEAD").stdout.strip()
    ctx.sh("git fetch -q upstream --tags && git update-ref refs/seclab/campaign-ci HEAD")
    pushed = os.path.join(os.path.dirname(ctx.repo), "pushed.git")
    ctx.sh('git init -q --bare "{0}" && git push -q --all "{0}" && git push -q --tags "{0}"'.format(pushed))
    ctx.expect("campaign-ci" not in ctx.sh('git --git-dir="%s" for-each-ref' % pushed).stdout,
               "git push --all and --tags don't send the old campaign's ref")
    ctx.sh('mkdir -p "$F/archive/ci" && mv "$F/CAMPAIGN.md" "$F/decisions.md" "$F/archive/ci/"')
    ctx.sh('python3 "%s" --branch new --base v1.1 > /dev/null' % os.path.join(SCRIPTS, "init.py"))
    ctx.block("release-rebase", old_base=toy, date="ci")
    branch = ctx.sh("git branch --show-current").stdout.strip()
    ctx.expect(branch.startswith("seclab/fuzz-")
               and ctx.sh("git rev-parse %s" % held_back).stdout.strip() == held_tip
               and ctx.sh("git merge-base --is-ancestor v1.1 HEAD", check=False).returncode == 0
               and os.path.exists(os.path.join(ctx.repo, "fuzz", "NOTES"))
               and ctx.sh("git diff --quiet v1.1 HEAD -- src/", check=False).returncode == 0,
               "the new campaign's branch (%s) starts at the release with the harness commits, without the fix, "
               "and %s keeps its fix" % (branch, held_back))
    head = ctx.sh("git rev-parse HEAD").stdout.strip()
    ctx.block("release-rebase", old_base="refs/seclab/campaign-ci", date="ci")
    ctx.expect(ctx.sh("git rev-parse HEAD").stdout.strip() == head, "with no commits to bring over, nothing happens")


DOC_API = """#include <stdlib.h>
#include <string.h>
typedef struct doc { char *text; size_t n; int mode; } doc_t;
static doc_t *doc_parse(const char *text, size_t n) {
  if (n == 0) return NULL;
  doc_t *d = (doc_t *)calloc(1, sizeof *d);
  d->text = (char *)malloc(n);
  memcpy(d->text, text, n);
  d->n = n;
  return d;
}
static void doc_set_mode(doc_t *d, int mode) { d->mode = mode; }
static size_t doc_count(const doc_t *d) { return d->n; }
static char doc_get(const doc_t *d, size_t i) { return d->text[i]; }
static void doc_free(doc_t *d) { free(d->text); free(d); }
"""


def call_sequence(ctx: Context, cflags: str, ldflags: str) -> None:
    head = "#include <fuzzer/FuzzedDataProvider.h>\n#include <string>\n#include <vector>\n" + DOC_API
    text = ctx.blocks["c-call-sequence"].text
    broken = text.replace("docs.erase(docs.begin() + i);", "")
    ctx.expect(broken != text, "the idiom removes a freed handle from its table")
    for name, body in (("fuzz_sequence", text), ("fuzz_sequence_broken", broken)):
        with open(os.path.join(ctx.repo, "fuzz", name + ".cc"), "w") as handle:
            handle.write(head + body)
        ctx.sh('"%s" %s %s fuzz/%s.cc -o "$F/build/libfuzzer/%s"' % (os.path.join(llvm_bin(), "clang++"), cflags, ldflags, name, name))
    run = '. .seclab/fuzz/env.sh; ASAN_OPTIONS="$ASAN_OPTIONS" "$F/build/libfuzzer/%s" -runs=20000 -seed=1 -max_len=256'
    clean = ctx.sh(run % "fuzz_sequence", check=False)
    ctx.expect(clean.returncode == 0 and "Done 20000 runs" in clean.stderr,
               "the call-sequence idiom runs 20000 inputs without a report")
    broken = ctx.sh(run % "fuzz_sequence_broken", check=False)
    ctx.expect(broken.returncode != 0 and re.search(r"heap-use-after-free|attempting double-free", broken.stderr),
               "without the table's bookkeeping, ASan reports a use-after-free or double free")


def aflpp_bin() -> str:
    if os.environ.get("AFLPP_BIN"):
        return os.environ["AFLPP_BIN"]
    found = shutil.which("afl-fuzz")
    if not found:
        raise DocError("afl-fuzz not found; set AFLPP_BIN")
    return os.path.dirname(found)


def build_aflpp_toy(ctx: Context) -> None:
    """The aflpp-plain, aflpp and cmplog variants, through build.md's compiler wrappers."""
    bindir = aflpp_bin()
    lto = system() == "Linux" and os.path.exists(os.path.join(bindir, "afl-clang-lto"))
    compiler = "afl-clang-lto" if lto else "afl-clang-fast"
    ctx.block("aflpp-wrappers", afl_cc=compiler)
    for variant, cc in (("aflpp-plain", compiler), ("aflpp", "$F/build/bin/aflpp-cc"), ("cmplog", "$F/build/bin/cmplog-cc")):
        ctx.sh(
            'export PATH="{b}:$PATH" && out="$F/build/{v}" && mkdir -p "$out" && "{w}" -g -O1 -c src/record.c -o "$out/record.o" && '
            '"{w}" -g -O1 -fsanitize=fuzzer fuzz/fuzz_record.c "$out/record.o" -o "$out/fuzz_record"'.format(
                b=bindir, v=variant, w=cc
            )
        )


def ci_template(heading: str) -> tuple:
    """({env}, {step name: run script}) of a workflow template in
    fuzz-campaign/references/ci.md, read by its simple layout (no YAML
    parser in the standard library)."""
    with open(os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "references", "ci.md")) as handle:
        section = handle.read().split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0]
    lines = re.search(r"```yaml\n(.*?)```", section, re.S).group(1).splitlines()
    env, steps, name, index = {}, {}, None, 0
    while index < len(lines):
        line = lines[index]
        indent = len(line) - len(line.lstrip())
        if line.strip() == "env:" and indent == 4:
            index += 1
            while index < len(lines) and len(lines[index]) - len(lines[index].lstrip()) == 6:
                if not lines[index].strip().startswith("#"):
                    key, value = lines[index].strip().split(": ", 1)
                    env[key] = value
                index += 1
            continue
        step = re.match(r"\s*- name: (.+)$", line)
        if step:
            name = step.group(1)
        run = re.match(r"\s*run: (.*)$", line)
        if run and name:
            if run.group(1) != "|":
                steps[name] = run.group(1)
            else:
                body = []
                while index + 1 < len(lines) and len(lines[index + 1]) - len(lines[index + 1].lstrip()) > indent:
                    index += 1
                    body.append(lines[index][indent + 2 :])
                steps[name] = "\n".join(body) + "\n"
        index += 1
    return env, steps


@scenario("c-cmake", "c", ["c-ub", "c-cmake-build", "c-build-check", "c-hooks-check", "build-worktree", "c-intree-build"])
def c_cmake(ctx: Context) -> None:
    llvm = llvm_bin()
    cflags, ldflags = c_flags(ctx, "libfuzzer")
    ctx.block("c-cmake-build", variant="libfuzzer", llvm_bin=llvm, compile_flags=cflags, link_flags=ldflags,
              fuzzers_option="RECORD_BUILD_FUZZERS", harness="record_fuzzer")
    # The toy sets CMAKE_BUILD_TYPE=Release when it's empty.
    ctx.block("c-build-check", variant="libfuzzer")
    ctx.say("   ok: the recipe keeps -O3 -DNDEBUG out of a forced Release build")
    ctx.sh('cmake -S . -B "$F/build/forced" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DCMAKE_C_COMPILER="%s/clang" '
           '-DCMAKE_BUILD_TYPE= -DCMAKE_C_FLAGS="%s" > /dev/null' % (llvm, cflags))
    ctx.block("c-build-check", variant="forced", ok=(1,))
    ctx.say("   ok: the check catches the build type's -O3 -DNDEBUG without the override")
    out = ctx.block("c-hooks-check", library='"$F/build/libfuzzer/librecord.a"').stdout
    counts = {k: int(v) for k, v in re.findall(r"(\w+) (\d+)", out)}
    ctx.expect(counts["sanitizer_cov"] > 0 and counts["__asan_"] > 0 and counts["__profc_"] == 0,
               "the library itself has the libfuzzer variant's hooks (%s)" % out.strip())

    # The C/C++ CI template, run step by step with its own environment.
    env, steps = ci_template("C/C++ with libFuzzer")
    build = re.search(r"e\.g\. (.*)>$", steps["Build fuzzers"]).group(1).replace("<PROJECT>", "RECORD")
    github_env = os.path.join(ctx.F, "github_env")
    ctx.sh("GITHUB_ENV=%s\n%s" % (shlex.quote(github_env), steps["Sanitizer flags"]))
    with open(github_env) as handle:
        env.update(line.rstrip("\n").split("=", 1) for line in handle if "=" in line)
    exports = "export PATH=%s:\"$PATH\" SECONDS_PER_HARNESS=60 %s\n" % (
        llvm, " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in env.items() if not v.startswith("${{")))
    ctx.sh('mkdir -p fuzz/corpus/record_fuzzer && cp seeds/ok fuzz/corpus/record_fuzzer/')
    ctx.sh(exports + build)
    ctx.sh("set -e\n" + exports + steps["Replay the corpus"])
    ctx.say("   ok: the CI template builds and replays the corpus")
    fuzz = ctx.sh("set -e\n" + exports + steps["Fuzz"], check=False)
    found = [f for f in ctx.files("fuzz-artifacts") if f.startswith("record_fuzzer-crash-")]
    ctx.expect(fuzz.returncode == 1 and found, "the CI template's fuzz step fails on the planted bug and keeps it")
    ctx.expect("Assertion" in fuzz.stdout + fuzz.stderr, "the CI build keeps assertions (no -DNDEBUG)")

    # An in-tree build (the toy's Makefile): a worktree synced to the uncommitted working tree without
    # a git identity, its outputs linked into $F/build/<variant>/, its coverage paths naming the checkout.
    # On macOS the work directory's path goes through a symlink (/var -> /private/var).
    ctx.sh("printf '/* uncommitted */\\n' >> src/record.c")
    head = ctx.sh("git rev-parse HEAD").stdout
    saved = dict(ctx.env)
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        ctx.env.pop(key, None)
    ctx.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="1",
                   GIT_CONFIG_KEY_0="user.useConfigOnly", GIT_CONFIG_VALUE_0="true")
    tree = dict(checkout=ctx.repo, repo=os.path.basename(ctx.repo), variant="coverage")
    ctx.block("build-worktree", **tree)
    ctx.env = saved
    worktree = os.path.join(os.path.dirname(ctx.repo), os.path.basename(ctx.repo) + ".seclab-worktrees", "build", "coverage")
    with open(os.path.join(worktree, "src", "record.c")) as handle:
        synced = "/* uncommitted */" in handle.read()
    ctx.expect(synced and ctx.sh("git rev-parse HEAD").stdout == head and "M src/record.c" in ctx.sh("git status --porcelain").stdout,
               "the worktree has the uncommitted change; the checkout's branch and changes stay")
    cflags, ldflags = c_flags(ctx, "coverage")
    ctx.block("c-intree-build", llvm_bin=llvm, compile_flags=cflags, link_flags=ldflags, targets="fuzz_record",
              binary="fuzz_record", library="librecord.a", **tree)
    ctx.sh('LLVM_PROFILE_FILE="$F/coverage/intree.profraw" "$F/build/coverage/fuzz_record" seeds/ok && '
           '"{0}/llvm-profdata" merge -sparse "$F/coverage/intree.profraw" -o "$F/coverage/intree.profdata" && '
           '"{0}/llvm-cov" export -format=lcov -instr-profile="$F/coverage/intree.profdata" "$F/build/coverage/fuzz_record" '
           '> "$F/coverage/intree.lcov"'.format(llvm))
    ctx.expect_covered("$F/coverage/intree.lcov", "src/record.c")


@scenario("c-aflpp", "c", ["c-env", "c-ub", "c-toolchain-check", "aflpp-wrappers", "aflpp-spec", "c-check-run",
                          "c-repro", "aflpp-merge", "aflpp-lto-check"],
          systems=("Linux",))
def c_aflpp(ctx: Context) -> None:
    ctx.set(H="record")
    ctx.env_block("c-env", detect_leaks=1, llvm_bin=llvm_bin())
    ctx.env_block("c-ub")
    path = ctx.env["PATH"]
    ctx.env["PATH"] = aflpp_bin() + os.pathsep + path
    out = ctx.block("c-toolchain-check", llvm_bin=llvm_bin()).stdout
    ctx.env["PATH"] = path
    ctx.expect("libFuzzer ok" in out and "afl-fuzz++" in out, "the toolchain check builds a libFuzzer binary and finds AFL++")
    ctx.expect(not glob.glob(os.path.join(ctx.F, "build", "toolchain-check*")), "the toolchain check leaves no binary behind")
    build_c_toy(ctx, ("libfuzzer",))
    build_aflpp_toy(ctx)
    # afl-clang-lto: aflpp-plain's edge count exceeds the harness's alone; equal for a library built
    # without AFL++, which the objdump count (0 either way) can't tell.
    path = ctx.env["PATH"]
    ctx.env["PATH"] = aflpp_bin() + os.pathsep + path
    plain = '"$F/build/aflpp-plain/record%s.o"'
    ctx.sh('"%s" -g -O1 -c src/record.c -o %s && afl-clang-lto -g -O1 -fsanitize=fuzzer fuzz/fuzz_record.c %s '
           '-o "$F/build/aflpp-plain/fuzz_record_raw"' % (os.path.join(llvm_bin(), "clang"), plain % "-raw", plain % "-raw"))
    for binary, suffix in (("fuzz_record", ""), ("fuzz_record_raw", "-raw")):
        out = ctx.block("aflpp-lto-check", harness_file="fuzz/fuzz_record.c", binary=binary,
                        harness_command="afl-clang-lto -g -O1 -fsanitize=fuzzer fuzz/fuzz_record.c " + plain % suffix).stdout
        edges = [int(n) for n in re.findall(r"(?:library|alone) (\d+)", out)]
        ctx.expect(len(edges) == 2 and (edges[0] > edges[1]) == (binary == "fuzz_record"),
                   "the LTO check tells an instrumented library (%s) from the harness alone: %s" % (binary, out.strip()))
    ctx.env["PATH"] = path
    ctx.sh('mkdir -p "$F/corpus/$H" && cp seeds/ok "$F/corpus/$H/" && cp record.dict "$F/dict/$H.dict"')
    common = dict(binary="fuzz_record", memory_mb=2048)
    ctx.write_block(
        "aflpp-spec", ".seclab/fuzz/runs/record-long1/spec.json", harness="record", n=1, tree=ctx.repo, run_s=120,
        aflpp_bin=aflpp_bin(), llvm_bin=llvm_bin(), workspace=ctx.F, input_timeout_ms=10000,
        b=os.path.join(ctx.F, "build"), **common
    )
    ctx.sh('cp "$F/dict/$H.dict" "$F/runs/$H-long1/$H.dict"')
    # 3 instances: CI runners for private repositories have 2 cores.
    ctx.sh('$L start --skip-cpu-check --spec "$F/runs/$H-long1/spec.json"')
    meta = ctx.meta("record-long1")
    states = [c["state"] for c in meta["copies"]]
    findings = ctx.findings("record-long1")
    ctx.expect(
        findings and all(s == "finished" for s in states),
        "AFL++ instances keep going past crashes (%s, %d findings)" % (", ".join(states), len(findings or [])),
    )
    ctx.expect(findings and not any(f.endswith("README.txt") for f in findings), "findings are crash files only")
    logs = [ctx.log("record-long1", i) for i in range(len(states))]
    ctx.expect(not any("Mistyped AFL environment variable" in log for log in logs)
               and all("Enabled environment variable %s " % v in logs[0] for v in ("AFL_CMPLOG_ONLY_NEW", "AFL_FAST_CAL")),
               "AFL++ takes the spec's variables, the resume settings included")
    path = ctx.env["PATH"]
    ctx.env["PATH"] = aflpp_bin() + os.pathsep + path
    out = ctx.block("c-check-run", id="record-long1").stdout
    ctx.env["PATH"] = path
    ctx.expect("record-long1" in out and "Summary stats" in out, "the run check shows the run and AFL++'s summary")

    # An AFL++ crash reproduces on the libFuzzer build (same harness).
    ctx.block("c-repro", n=1, timeout_s=60, finding=findings[0], input_timeout_s=10, ok=(1,), **common)
    ctx.expect("AddressSanitizer" in ctx.log("repro-record-1"), "the libFuzzer build reproduces the AFL++ crash")

    ctx.block("aflpp-merge", n=1, timeout_s=300, long_run="record-long1", input_timeout_s=10, max_len=4096, **common)
    ctx.expect_state("merge-record-1", "finished")
    ctx.expect(len(ctx.files(".seclab/fuzz/runs/merge-record-1/corpus")) >= 2, "the merge keeps the queues' inputs")


def java_home() -> str:
    """JAZZER_JAVA_HOME, or JAVA_HOME, or macOS's JDK 21."""
    for key in ("JAZZER_JAVA_HOME", "JAVA_HOME"):
        if os.environ.get(key):
            return os.environ[key]
    if system() == "Darwin":
        return subprocess.run(["/usr/libexec/java_home", "-v", "21"], capture_output=True, text=True).stdout.strip()
    raise DocError("set JAVA_HOME to a JDK Jazzer supports (21)")


def apply_maven_recipe(ctx: Context) -> None:
    """Add fuzz-java build.md's Maven dependencies to the toy's API-only pom, as an agent would."""
    with open(os.path.join(SKILLS, "fuzzing", "fuzz-java", "references", "build.md")) as handle:
        recipe = re.search(r"^Maven:\n\n```xml\n(.*?)^```", handle.read(), re.M | re.S).group(1)
    managed, deps = recipe.split("<!-- in <dependencies>: -->\n")
    pom = os.path.join(ctx.repo, "record", "pom.xml")
    with open(pom) as handle:
        text = handle.read()
    indent = lambda block, n: "".join(" " * n + line + "\n" for line in block.strip("\n").splitlines())
    text = text.replace("  <dependencies>\n", indent(managed, 2) + "  <dependencies>\n" + indent(deps, 4), 1)
    with open(pom, "w") as handle:
        handle.write(text)
    ctx.say("== Maven recipe (skills/fuzzing/fuzz-java/references/build.md) -> record/pom.xml")


def build_java_toy(ctx: Context) -> None:
    """Compile and write the classpath file, in the toy's Maven project (record/)."""
    ctx.block("java-maven-classpath", subdir="record", module_dir=os.path.join(ctx.repo, "record"), module="record")


@scenario(
    "java",
    "java",
    ["java-toolchain-check", "java-env", "jacoco-setup", "java-develop", "java-instrumented", "java-repro",
     "java-minimize", "java-reproducer", "java-long", "java-cov-replay", "java-cov-export", "java-merge",
     "java-junit", "java-reach", "java-leak-check", "java-check-run", "handoff-held-back",
     "java-maven-classpath", "java-decode"],
)
def java(ctx: Context) -> None:
    home = java_home()
    ctx.set(H="record", JAVA_HOME=home)
    # The toolchain check runs before the first-build notice: no wrapper and no project setting may run.
    ctx.sh("mkdir .mvn && echo -javaagent:/nonexistent-seclab-agent.jar > .mvn/jvm.config")
    for wrapper in ("mvnw", "gradlew"):
        ctx.sh("printf '#!/bin/sh\\ntouch wrapper-ran\\n' > %s && chmod +x %s" % (wrapper, wrapper))
    check = ctx.block("java-toolchain-check", ok=(0, 127))  # 127: no system Gradle
    out = check.stdout + check.stderr
    ctx.expect("Apache Maven" in out and "nonexistent-seclab-agent" not in out,
               "the toolchain check runs mvn -v without the project's .mvn/jvm.config")
    ctx.expect("mvnw" in check.stdout and "gradlew" in check.stdout and not os.path.exists(os.path.join(ctx.repo, "wrapper-ran")),
               "the toolchain check finds the wrappers without running them")
    ctx.sh("rm -r .mvn mvnw gradlew")
    apply_maven_recipe(ctx)
    build_java_toy(ctx)
    ctx.env_block("java-env", java_home=home, module="record", memory_mb=512)
    out = ctx.sh("unset JAVA_HOME; . .seclab/fuzz/env.sh; sh -c 'echo \"$JAVA_HOME\"'").stdout.strip()
    ctx.expect(out == home, "env.sh exports JAVA_HOME to the build tools")
    ctx.block("jacoco-setup")
    ctx.sh('mkdir -p "$F/corpus/$H" && cp seeds/ok "$F/corpus/$H/" && cp record.dict "$F/dict/$H.dict"')
    common = dict(fuzz_class="toy.RecordFuzzTest", fuzz_method="parse", memory_mb=512, input_timeout_s=10)

    ctx.block("java-develop", round=1, run_s=120, **common)
    ctx.expect_state("r1-record", "found-crash")
    finding = ctx.findings("r1-record")[0]
    ctx.expect("r1-record" in ctx.block("java-check-run", id="r1-record").stdout, "the run check shows the run")
    ctx.expect(not glob.glob(os.path.join(ctx.repo, "record", "src", "test", "resources", "**", "crash-*"), recursive=True),
               "no crash file lands in src/test/resources")
    ctx.block("java-instrumented", run_id="r1-record", target_classes="toy.Record")
    # A target excluded from coverage instrumentation still gets hooks: the check must reject it.
    ctx.sh('$L start --id blind-$H --harness $H --timeout 120 --env JAVA_HOME="$JAVA_HOME" -- "$JAVA_HOME/bin/java" '
           '-cp "$(cat "$F/build/jazzer/record.classpath")" com.code_intelligence.jazzer.Jazzer '
           '--target_class=toy.RecordFuzzTest --target_method=parse --instrumentation_excludes=toy.Record '
           '-runs=100 -rss_limit_mb=0 "$PWD/seeds/ok"')
    ctx.expect("toy.Record with custom hooks only" in ctx.log("blind-record"), "an excluded target gets hooks only")
    out = ctx.block("java-instrumented", ok=(1,), run_id="blind-record", target_classes="toy.Record").stdout
    ctx.expect("no coverage feedback: toy.Record" in out, "the instrumentation check rejects hooks-only instrumentation")
    # A queued finding skipped by its DEDUP_TOKEN (triage.md): the planted bug no longer ends a develop run.
    token = re.search(r"^DEDUP_TOKEN: ([0-9a-f]{16})$", ctx.log("r1-record"), re.M).group(1)
    ctx.sh('mkdir -p "$F/corpus/skip" && cp "$F/corpus/$H"/* "$F/corpus/skip/" && cp "$F/dict/$H.dict" "$F/dict/skip.dict"')
    ctx.set(H="skip")
    ctx.block("java-develop", round=1, run_s=20, **dict(common, fuzz_method="parse --ignore=" + token))
    ctx.expect_state("r1-skip", "finished")
    ctx.set(H="record")

    ctx.block("java-repro", n=1, timeout_s=120, finding=finding, ok=(77,), **common)
    ctx.expect("StringIndexOutOfBoundsException" in ctx.log("repro-record-1"), "the reproduction prints the exception")
    buckets = ctx.sh('python3 "$F/bin/crash_buckets.py" "$F"/runs/repro-*/copy-*/log --root .').stdout
    ctx.expect("Record.parse" in buckets, "crash_buckets.py names the project frame")
    # The project's junit-platform.properties configures standalone runs too (build.md): a jazzer.ignore there hides the bug.
    props = os.path.join(ctx.repo, "record", "target", "test-classes", "junit-platform.properties")
    with open(props, "w") as handle:
        handle.write("jazzer.ignore=%s\n" % re.search(r"^DEDUP_TOKEN: (\w+)$", ctx.log("repro-record-1"), re.M).group(1))
    ctx.block("java-repro", n=8, timeout_s=120, finding=finding, **common)
    ctx.expect_state("repro-record-8", "finished")
    os.remove(props)
    ctx.block("java-minimize", n=1, timeout_s=300, minimize_s=240, bucket="sioobe", finding=finding, **common)
    minimized = os.path.join(ctx.F, "crashes", "sioobe", "minimized")
    ctx.block("java-repro", n=9, timeout_s=120, finding=minimized, ok=(77,), **common)
    ctx.expect("StringIndexOutOfBoundsException" in ctx.log("repro-record-9"), "the minimized input reproduces")
    repeat = documented("fuzz-java", "triage.md", r"`(-runs=\d+)` before the file")
    ctx.block("java-repro", n=10, timeout_s=120, finding=os.path.join(ctx.repo, "seeds", "ok"), add=('"<finding>"', repeat + " "), **common)
    expect_repeat(ctx, "repro-record-10", repeat)
    # A typed fuzz test's crash file, decoded without running the test, through the launcher (decoding runs
    # project code): the values that crash.
    ctx.set(H="typed")
    ctx.sh('mkdir -p "$F/corpus/$H" && cp record.dict "$F/dict/$H.dict"')
    ctx.block("java-develop", round=1, run_s=60, **dict(common, fuzz_method="parseTyped"))
    ctx.expect_state("r1-typed", "found-crash")
    ctx.block("java-decode", n=1, timeout_s=120, fuzz_class="toy.RecordFuzzTest", fuzz_method="parseTyped",
              finding=ctx.findings("r1-typed")[0])
    ctx.expect_state("decode-typed-1", "finished")
    out = [line for line in ctx.log("decode-typed-1").splitlines() if not line.startswith("Picked up JAVA_TOOL_OPTIONS")]
    chars = len(re.findall(r"\\u\{[0-9a-f]+\}|.", out[0])) if len(out) == 2 else 0
    ctx.expect(len(out) == 2 and int(out[1]) > chars,
               "the decoder prints the body and a length past its end (%s)" % " / ".join(out))
    ctx.set(H="record")
    ctx.sh('cp RecordRegressionTest.java "$F/crashes/sioobe/"')
    reproducer = dict(bucket="sioobe", repro_class="RecordRegressionTest", timeout_s=120, memory_mb=512)
    ctx.block("java-reproducer", n=1, ok=(1,), **reproducer)
    ctx.expect("StringIndexOutOfBoundsException" in ctx.log("poc-record-1"), "the reproducer fails before the fix")

    # The Java CI template's fuzz steps, on the module, before the fix.
    _env, steps = ci_template("Java with Jazzer (JUnit `@FuzzTest`)")
    subst = {"<FuzzTestClass>": "RecordFuzzTest", "<package>": "toy", "<method>": "parse"}
    script = "\n".join(steps[s] for s in ("Build the fuzz classpath", "Fuzz"))
    for key, value in subst.items():
        script = script.replace(key, value)
    script = re.sub(r"\$\{\{[^}]*\}\}", "60", script)
    seeds = os.path.join(ctx.repo, "record", "src", "test", "resources", "toy", "RecordFuzzTestInputs", "parse")
    ctx.sh('mkdir -p "%s" && cp seeds/ok "%s/"' % (seeds, seeds))
    fuzz = ctx.sh('cd record && export PATH="$JAVA_HOME/bin:$PATH"\n' + script, check=False)
    found = [f for f in ctx.files("record/fuzz-artifacts") if f.startswith("crash-")]
    ctx.expect(fuzz.returncode != 0 and found, "the Java CI template's fuzz step finds the planted bug and keeps it")
    ctx.expect("RecordFuzzTestInputs/parse" in fuzz.stdout + fuzz.stderr,
               "it seeds from the inputs directory regression mode replays")

    ctx.block("java-long", n=1, copies=2, run_s=30, tree=ctx.repo, **common)
    ctx.sh('cp "$F/dict/$H.dict" "$F/runs/$H-long1/$H.dict"')
    ctx.sh('$L start --spec "$F/runs/$H-long1/spec.json"')
    ctx.expect_state("record-long1", "found-crash", "finished")

    ctx.sh('cp "%s" "$F/corpus/$H/"' % finding)
    ctx.block("java-cov-replay", n=1, timeout_s=300, **common)
    ctx.expect_state("cov-record-1", "finished")
    ctx.expect(len(ctx.findings("cov-record-1")) == 1, "the replay continues past the crashing input and records it")
    ctx.block("java-cov-export", n=1, round=1, module="record")
    ctx.expect_covered("$F/coverage/$H/r1.lcov", "record/src/main/java/toy/Record.java")
    # Hand-off on a JaCoCo-derived lcov: a harness reaching a held-back bug's line is held back.
    ctx.sh('mkdir -p "$F/handoff" && printf "id\\tfile\\tline\\nsioobe\\trecord/src/main/java/toy/Record.java\\t14\\n" '
           '> "$F/handoff/held-back.tsv"')
    out = ctx.block("handoff-held-back", h="record", last="r1").stdout
    ctx.expect("| sioobe | - | record/src/main/java/toy/Record.java:14 | reached |" in out,
               "the hand-off check finds the harness reaching the held-back line in JaCoCo's lcov")

    # W1, as for C/C++: the merge's replay finds the bug; after the fix, nothing.
    ctx.block("java-merge", n=1, timeout_s=300, long_run="record-long1", **common)
    ctx.expect(len(ctx.findings("merge-record-1")) >= 1, "the first merge reports the crashing input")

    # reach.py with the crashing input in the corpus: Jazzer writes no finding file for inputs given
    # as files, and the replay's exit status shows it.
    out = ctx.block("java-reach", file="record/src/main/java/toy/Record.java", line=14, timeout_s=300,
                    module="record", **common).stdout
    ctx.expect("reached: " in out, "reach.py finds an input that reaches Record.java:14")
    ctx.expect("exited 77" in out, "reach.py reports the input that throws in its replays")

    # JUnit regression mode through the launcher replays the inputs directory (run.md), here with the crash file.
    # It runs offline: fetch Surefire's JUnit provider first.
    ctx.sh('cd record && mvn -B -q test -Dtest=toy.RecordFuzzTest#parse')
    inputs = os.path.join(ctx.repo, "record", "src", "test", "resources", "toy", "RecordFuzzTestInputs")
    os.makedirs(os.path.join(inputs, "parse"), exist_ok=True)
    shutil.copy(finding, os.path.join(inputs, "parse"))
    junit = dict(timeout_s=300, module_dir=os.path.join(ctx.repo, "record"), home=os.path.expanduser("~"), **common)
    ctx.block("java-junit", run_id="repro-record-2", ok=(1,), **junit)
    ctx.expect("StringIndexOutOfBoundsException" in ctx.log("repro-record-2"), "JUnit regression mode replays the crash file")
    ctx.expect(re.search(r"Picked up JAVA_TOOL_OPTIONS: .*-Xmx512m.*-XX:-OmitStackTraceInFastThrow", ctx.log("repro-record-2")),
               "the JUnit run's JVMs get JOPTS")

    ctx.sh("git apply fix.patch")
    build_java_toy(ctx)
    ctx.block("java-junit", run_id="repro-record-3", **junit)
    ctx.expect_state("repro-record-3", "finished")
    shutil.rmtree(inputs)
    shutil.rmtree(os.path.join(ctx.repo, "record", "target", "test-classes", "toy", "RecordFuzzTestInputs"))
    ctx.block("java-reproducer", n=2, **reproducer)
    ctx.expect_state("poc-record-2", "finished")
    # A stopped JVM's exit hook writes its input in flight: that's no finding.
    ctx.sh('. .seclab/fuzz/env.sh; $L start --id stop-$H-1 --harness $H --time 600 --detach --env PATH="$RUN_PATH" '
           '--env JAVA_HOME="$JAVA_HOME" -- "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" com.code_intelligence.jazzer.Jazzer '
           '--target_class=toy.RecordFuzzTest --target_method=parse -max_total_time=0 -rss_limit_mb=0 '
           '-artifact_prefix="{run}/artifacts/copy-{copy}/" "{run}/corpus" "$F/corpus/$H"')
    time.sleep(15)
    ctx.sh(". .seclab/fuzz/env.sh; $L stop stop-$H-1")
    ctx.expect_state("stop-record-1", "stopped")
    late = sum(len(c.get("after_stop", [])) for c in ctx.meta("stop-record-1")["copies"])
    ctx.expect(ctx.meta("stop-record-1")["findings_total"] == 0,
               "a stopped run leaves no findings (%d file(s) written after the stop)" % late)
    ctx.block("java-merge", n=2, timeout_s=300, long_run="record-long1", **common)
    ctx.expect(ctx.findings("merge-record-2") == [], "the merge on the fixed build finds nothing")
    crash_name = os.path.basename(finding)[len("crash-"):]
    ctx.expect(crash_name in ctx.files(".seclab/fuzz/runs/merge-record-2/corpus"), "the fixed bug's input is kept")
    ctx.expect("MERGE-OUTER: attempt 2" not in ctx.log("merge-record-2"), "the inner merge runs succeed")

    ctx.sh('rm -rf "$F/corpus/$H" && mv "$F/runs/merge-$H-2/corpus" "$F/corpus/$H"')

    # The gate's leak check: clean for this harness, a difference for a leaky one.
    out = ctx.block("java-leak-check", n=1, sample_size=8, timeout_s=300, module="record", **common).stdout
    ctx.expect("cover exactly the same" in out, "the leak check finds nothing in a clean harness")
    ctx.sh('mkdir -p "$F/corpus/leaky" && cp "$F/corpus/$H"/* "$F/corpus/leaky/"')
    ctx.set(H="leaky")
    out = ctx.block("java-leak-check", n=1, sample_size=8, timeout_s=300, module="record",
                    **dict(common, fuzz_method="parseLeaky")).stdout
    ctx.expect("### Only A" in out and "RecordFuzzTest.java" in out, "the leak check points at the leaking harness")

    # Detector gaps (harness.md's table): the built-in detector misses a command run through a shell.
    shutil.copy(os.path.join(ctx.repo, "ToolsFuzzTest.java.in"),
                os.path.join(ctx.repo, "record", "src", "test", "java", "toy", "ToolsFuzzTest.java"))
    build_java_toy(ctx)
    ctx.set(H="tools")
    ctx.sh('printf "x;jazze" > cmd-input')
    ctx.block("java-repro", n=1, timeout_s=120, finding=os.path.join(ctx.repo, "cmd-input"),
              **dict(common, fuzz_class="toy.ToolsFuzzTest", fuzz_method="greet"))
    ctx.expect_state("repro-tools-1", "finished")
    ctx.say("   ok: the built-in command detector misses sh -c")


@scenario("js-ts", "js-ts", ["js-env", "js-ts-build", "js-cli-develop"])
def js_ts(ctx: Context) -> None:
    """An ESM package in TypeScript (bundler resolution, extensionless imports): the CLI target fuzzes its build."""
    ctx.sh('PATH="%s:$PATH" npm install --no-audit --no-fund --loglevel=error' % node_bin())
    ctx.env_block("js-env", node_bin=node_bin(), package_dir=ctx.repo)
    ctx.block("js-ts-build", entry="src/record.ts")
    ctx.expect(os.path.exists(os.path.join(ctx.F, "build", "ts", "src", "record.js")), "tsc writes the target's .js files")
    built = glob.glob(os.path.join(ctx.F, "build", "ts", "**", "*.js"), recursive=True)
    ctx.expect(not [p for p in built if p.endswith(".test.js")], "the build leaves the project's tests out")
    ctx.set(H="record-ts")
    ctx.sh('mkdir -p "$F/corpus/$H" && cp seeds/ok "$F/corpus/$H/" && touch "$F/dict/$H.dict"')
    ctx.block("js-cli-develop", round=1, run_s=60, target_file="fuzz/record.fuzz.js", input_timeout_ms=5000, memory_mb=2048)
    ctx.expect_state("r1-record-ts", "found-crash")
    ctx.expect("TypeError" in ctx.log("r1-record-ts"), "the target loads the build and finds the planted bug")


@scenario("js-glibc", "js", ["js-glibc-check"])
def js_glibc(ctx: Context) -> None:
    """The glibc check, wherever it runs; CI runs it on Debian 12 (glibc 2.36) as well."""
    out = ctx.block("js-glibc-check").stdout
    libc, version = platform.libc_ver()
    if system() != "Linux":
        ctx.expect(out == "", "the glibc check is silent outside Linux")
        return
    old = libc != "glibc" or tuple(int(x) for x in version.split(".")[:2]) < (2, 38)
    ctx.expect(("too old" in out) == old, "the glibc check says %r for %s %s" % (out.strip(), libc, version))


def java_gradle(ctx: Context, task: str, module: str, fuzz_class: str, method: str, classes: str, jopts: str) -> None:
    """A Gradle build: the classpath recipe, then the gate's instrumentation check and a develop round."""
    home = java_home()
    ctx.set(JAVA_HOME=home, H=module)
    ctx.block("java-gradle-classpath", gradle="gradle", task=task, module=module)
    with open(os.path.join(ctx.F, "build", "jazzer", module + ".classpath")) as handle:
        entries = handle.read().strip().split(os.pathsep)
    missing = [e for e in entries if e.endswith(".jar") and not os.path.exists(e)]
    ctx.expect(entries and not missing, "every JAR on the classpath exists (%d entries; missing: %s)" % (len(entries), missing))
    ctx.env_block("java-env", java_home=home, module=module, memory_mb=512)
    if jopts:
        with open(os.path.join(ctx.F, "env.sh"), "a") as handle:
            handle.write('JOPTS="$JOPTS %s"\n' % jopts)
    ctx.sh('mkdir -p "$F/corpus/$H" && cp seeds/ok "$F/corpus/$H/" && echo \'"ab"\' > "$F/dict/$H.dict"')
    ctx.block("java-develop", round=1, run_s=60, fuzz_class=fuzz_class, fuzz_method=method, input_timeout_s=10, memory_mb=512)
    ctx.expect_state("r1-" + module, "found-crash")
    ctx.block("java-instrumented", run_id="r1-" + module, target_classes=classes)


@scenario("java-gradle", "java-gradle", ["java-gradle-classpath", "java-env", "java-develop", "java-instrumented"])
def java_gradle_multi(ctx: Context) -> None:
    """Two modules: the fuzz test in app, the bug in core, which reaches the classpath as a JAR."""
    java_gradle(ctx, ":app:seclabTestClasspath", "app", "app.ReaderFuzzTest", "read", "app.Reader core.Magic",
                "-Djazzer.instrument=app.**,core.**")


@scenario("java-gradle-single", "java-gradle-single", ["java-gradle-classpath", "java-env", "java-develop", "java-instrumented"])
def java_gradle_single(ctx: Context) -> None:
    java_gradle(ctx, ":seclabTestClasspath", "toy", "toy.MagicFuzzTest", "check", "toy.Magic", "")


def clean_leak_check(out: str) -> bool:
    """Same hit counts, or differences only in code that runs once per process (JS module loading)."""
    return ("same hit count on every branch and line" in out or "differ only on code that runs once per process" in out) \
        and "### Different hit counts" not in out


def node_bin() -> str:
    if os.environ.get("NODE_BIN"):
        return os.environ["NODE_BIN"]
    found = shutil.which("node")
    if not found:
        raise DocError("node not found; set NODE_BIN")
    return os.path.dirname(found)


@scenario(
    "js",
    "js",
    ["js-toolchain-check", "js-env", "jest-fuzz-config", "jest-probe", "jest-own-run", "jest-develop", "jest-repro", "jest-cov-replay", "js-cov-export", "jest-reach",
     "js-cli-develop", "js-cli-repro", "js-cli-minimize", "js-reproducer", "js-cli-cov-replay", "js-cli-long", "js-cli-merge",
     "js-cli-reach", "js-cli-leak-check", "js-check-run", "redos-check", "js-hang-profile",
     "js-pp-tripwire", "js-http", "js-feedback-check", "js-object-input", "js-shadowing"],
)
def js(ctx: Context) -> None:
    # The toolchain check runs before the first-build notice: Yarn must not run the release a .yarnrc names.
    ctx.sh("""printf 'require("fs").writeFileSync(%s, "")\\n' > yarn-canary.js""" % json.dumps(os.path.join(ctx.repo, "yarn-ran")))
    ctx.sh("""printf 'yarn-path "./yarn-canary.js"\\n' > .yarnrc && printf 'yarnPath: ./yarn-canary.js\\n' > .yarnrc.yml""")
    path = ctx.env["PATH"]
    ctx.env["PATH"] = node_bin() + os.pathsep + path
    if not shutil.which("yarn", path=ctx.env["PATH"]):
        shim = os.path.join(os.path.dirname(ctx.repo), "yarn-shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "yarn"), "w") as handle:
            handle.write('#!/bin/sh\nexec npx -y yarn@1.22.22 "$@"\n')
        os.chmod(os.path.join(shim, "yarn"), 0o755)
        ctx.env["PATH"] = shim + os.pathsep + ctx.env["PATH"]
    out = ctx.block("js-toolchain-check").stdout
    ctx.env["PATH"] = path
    ctx.expect(len(re.findall(r"^v?\d+\.\d+\.\d+\s*$", out, re.M)) >= 3,
               "the toolchain check prints the Node.js, npm and Yarn versions")
    ctx.expect(not os.path.exists(os.path.join(ctx.repo, "yarn-ran")), "the toolchain check runs no Yarn release from the project")
    ctx.sh("rm yarn-canary.js .yarnrc .yarnrc.yml")
    ctx.sh('PATH="%s:$PATH" npm install --no-audit --no-fund --loglevel=error' % node_bin())
    ctx.env_block("js-env", node_bin=node_bin(), package_dir=ctx.repo)
    jest = dict(jest_config="jest.fuzz.config.js", test_file="src/record.fuzz.js", test_title="record parse",
                input_timeout_ms=5000, memory_mb=2048)
    corpus_dir = "record.fuzz/record/parse"
    regression_dir = "src/record.fuzz/record/parse"

    # Jest setup: the probe, the fuzz tests' own config, the project's own run left alone.
    ctx.set(H="record")
    ctx.write_block("jest-fuzz-config", "jest.fuzz.config.js", tests_dir="src")
    ctx.block("jest-probe", n=1)
    ctx.expect_state("jest-probe-1", "finished")
    log = ctx.log("jest-probe-1")
    ctx.expect("<empty>" in log and "Done 1000 runs" in log, "the probe runs a fuzz test in regression and in fuzzing mode")
    ctx.expect(not os.path.exists(os.path.join(ctx.F, "jest-probe")), "the probe removes its directory")
    out = ctx.block("jest-own-run", n=1).stdout.split()
    ctx.expect(out[-1:] == ["0"], "the project's own Jest run lists no fuzz test")
    ctx.sh("""echo 'module.exports = { testMatch: ["<rootDir>/src/**/*.js"] };' > jest.config.js""")
    out = ctx.block("jest-own-run", n=2).stdout.split()
    ctx.expect(out[-1:] == ["1"], "the check sees a project config that runs the fuzz test")
    ctx.sh("rm jest.config.js")

    # Jest: the persistent corpus is the package's .cifuzz-corpus.
    ctx.sh('mkdir -p ".cifuzz-corpus/%s" && cp seeds/ok ".cifuzz-corpus/%s/"' % (corpus_dir, corpus_dir))
    ctx.sh('cp record.dict "$F/dict/$H.dict"')
    ctx.block("jest-develop", round=1, run_s=120, corpus_dir=corpus_dir, **jest)
    ctx.expect_state("r1-record", "found-crash")
    finding = ctx.findings("r1-record")[0]
    ctx.expect(ctx.files(regression_dir) == [], "no crash file lands in the regression directory")
    ctx.sh('mkdir -p "%s" && cp "%s" "%s/repro-1"' % (regression_dir, finding, regression_dir))
    ctx.block("jest-repro", n=1, timeout_s=120, input_name="repro-1", ok=(1,), **jest)
    ctx.sh('rm "%s/repro-1"' % regression_dir)
    ctx.expect("TypeError" in ctx.log("repro-record-1"), "the reproduction prints the TypeError")
    buckets = ctx.sh('python3 "$F/bin/crash_buckets.py" "$F"/runs/repro-*/copy-*/log --root .').stdout
    ctx.expect("record.js" in buckets, "crash_buckets.py names the project file")
    ctx.block("jest-cov-replay", n=1, timeout_s=300, **jest)
    ctx.expect_state("cov-record-1", "finished")
    ctx.block("js-cov-export", n=1, round=1)
    ctx.expect_covered("$F/coverage/$H/r1.lcov", "src/record.js")
    out = ctx.block("jest-reach", corpus_dir=corpus_dir, regression_dir=regression_dir, file="src/record.js", line=7,
                    timeout_s=300, **jest).stdout
    ctx.expect("reached: " in out, "reach.py finds a Jest input that reaches src/record.js:7")
    ctx.sh('PATH="%s:$PATH" npm install --no-save --no-audit --no-fund --loglevel=error jest@30.5.2' % node_bin())
    ctx.block("jest-probe", n=2, ok=(1,))
    ctx.expect_state("jest-probe-2", "exited 1")
    ctx.expect("_buildTransformResult" in ctx.log("jest-probe-2"), "the probe fails on Jest 30.5 with the runner's known error")
    ctx.expect(not os.path.exists(os.path.join(ctx.F, "jest-probe")), "the failed probe removes its directory too")

    # CLI target.
    ctx.set(H="record-cli")
    ctx.sh('mkdir -p "$F/corpus/$H" && cp seeds/ok "$F/corpus/$H/" && cp record.dict "$F/dict/$H.dict"')
    cli = dict(target_file="fuzz/record.js", input_timeout_ms=5000, memory_mb=2048)
    ctx.block("js-cli-develop", round=1, run_s=120, **cli)
    ctx.expect_state("r1-record-cli", "found-crash")
    ctx.expect("synchronous mode seems beneficial" not in ctx.log("r1-record-cli"),
               "the synchronous target runs with --sync")
    ctx.expect("r1-record-cli" in ctx.block("js-check-run", id="r1-record-cli").stdout, "the run check shows the run")
    # Fuzzing feedback (build.md): on in the CLI and Jest develop runs; off with dryRun, whose
    # coverage replays still have the target's SF: lines.
    for run_id in ("r1-record", "r1-record-cli"):
        ctx.block("js-feedback-check", run_id=run_id)
        ctx.say("   ok: %s has fuzzing feedback" % run_id)
    ctx.sh('. .seclab/fuzz/env.sh; $L start --id dry-$H --harness $H --timeout 120 --env PATH="$RUN_PATH" '
           '--env JAZZER_DRY_RUN=true -- "$P/node_modules/.bin/jazzer" "$P/fuzz/record.js" "{run}/corpus" "$F/corpus/$H" '
           '--sync -- -runs=500', check=False)
    out = ctx.block("js-feedback-check", run_id="dry-record-cli", ok=(1,)).stdout
    ctx.expect("dryRun" in out, "the check finds no feedback in a dryRun run")
    finding = ctx.findings("r1-record-cli")[0]
    ctx.block("js-cli-repro", n=1, timeout_s=120, finding=finding, ok=(77,), **cli)
    buckets = ctx.sh('python3 "$F/bin/crash_buckets.py" "$F"/runs/repro-$H-1/copy-0/log --root .').stdout
    ctx.expect("parseRecord" in buckets, "crash_buckets.py names the CLI target's project frame")
    ctx.block("js-cli-minimize", n=1, timeout_s=300, bucket="typeerror", finding=finding, **cli)
    minimized = os.path.join(ctx.F, "crashes", "typeerror", "minimized")
    ctx.block("js-cli-repro", n=2, timeout_s=120, finding=minimized, ok=(77,), **cli)
    ctx.expect("TypeError" in ctx.log("repro-record-cli-2"), "the minimized input reproduces")
    repeat = documented("fuzz-js", "triage.md", r"`(-runs=\d+)` after the `--`")
    ctx.block("js-cli-repro", n=3, timeout_s=120, finding=os.path.join(ctx.repo, "seeds", "ok"), add=("-rss_limit_mb=", repeat + " "), **cli)
    expect_repeat(ctx, "repro-record-cli-3", repeat)
    ctx.sh('cp repro.js "$F/crashes/typeerror/repro.js"')
    ctx.block("js-reproducer", n=1, bucket="typeerror", timeout_s=120, memory_mb=2048, ok=(1,))
    ctx.expect("TypeError" in ctx.log("poc-record-cli-1"), "the reproducer fails before the fix")
    ctx.block("js-cli-cov-replay", n=1, timeout_s=300, **cli)
    ctx.expect_state("cov-record-cli-1", "finished")
    ctx.block("js-cov-export", n=1, round=1)
    ctx.expect_covered("$F/coverage/$H/r1.lcov", "src/record.js")

    # The JS CI template before the fix: the blocking replay passes on the seeds, the fuzz step
    # finds the planted bug, and the replay fails once the crashing input is in the corpus.
    _env, steps = ci_template("JS/TS with Jazzer.js (CLI targets)")

    def ci_step(step: str, target: str, thing: str) -> subprocess.CompletedProcess:
        script = steps[step].replace("fuzz/<thing>.fuzz.js", target).replace("<thing>", thing)
        return ctx.sh('export PATH="%s:$PATH"\n' % node_bin() + re.sub(r"\$\{\{[^}]*\}\}", "60", script), check=False)

    ctx.sh("mkdir -p fuzz/corpus/record && cp seeds/ok fuzz/corpus/record/")
    ctx.expect(ci_step("Replay the corpus", "fuzz/record.js", "record").returncode == 0,
               "the JS CI template's replay passes on the seeds")
    fuzz = ci_step("Fuzz", "fuzz/record.js", "record")
    found = [f for f in ctx.files("fuzz-artifacts") if f.startswith("crash-")]
    ctx.expect(fuzz.returncode != 0 and found, "the JS CI template's fuzz step finds the planted bug and keeps it")
    ctx.sh("cp fuzz-artifacts/%s fuzz/corpus/record/" % found[0])
    replay = ci_step("Replay the corpus", "fuzz/record.js", "record")
    ctx.expect(replay.returncode != 0 and "TypeError" in replay.stdout + replay.stderr,
               "the JS CI template's replay fails on a crashing corpus input")

    # W1 with a run corpus holding the crashing input.
    ctx.sh('mkdir -p "$F/runs/$H-long1/corpus" && cp "%s" "$F/runs/$H-long1/corpus/"' % finding)
    ctx.block("js-cli-merge", n=1, timeout_s=300, long_run="record-cli-long1", **cli)
    ctx.expect(len(ctx.findings("merge-record-cli-1")) >= 1, "the first merge reports the crashing input")
    ctx.sh("git apply fix.patch")
    ctx.block("js-reproducer", n=2, bucket="typeerror", timeout_s=120, memory_mb=2048)
    ctx.expect_state("poc-record-cli-2", "finished")
    ctx.block("js-cli-merge", n=2, timeout_s=300, long_run="record-cli-long1", **cli)
    ctx.expect(ctx.findings("merge-record-cli-2") == [], "the merge on the fixed code finds nothing")
    ctx.expect(len(ctx.files(".seclab/fuzz/runs/merge-record-cli-2/corpus")) >= 2, "the merge keeps inputs")
    out = ctx.block("js-cli-reach", file="src/record.js", line=7, timeout_s=300, **cli).stdout
    ctx.expect("reached: " in out, "reach.py finds a CLI input that reaches src/record.js:7")

    # Long run on the fixed code, with one seed: no finding stops a copy, and new inputs are certain.
    # They go to the run's corpus; the persistent corpus stays as it was.
    ctx.set(H="record-long")
    ctx.sh('mkdir -p "$F/corpus/$H" && cp seeds/ok "$F/corpus/$H/"')
    ctx.block("js-cli-long", n=1, copies=2, run_s=20, tree=ctx.repo, tree_package=ctx.repo, **cli)
    ctx.sh('cp record.dict "$F/runs/$H-long1/$H.dict"')
    ctx.sh('$L start --spec "$F/runs/$H-long1/spec.json"')
    meta = ctx.expect_state("record-long-long1", "finished")
    ctx.expect(len(meta["copies"]) == 2 and ctx.files(".seclab/fuzz/runs/record-long-long1/corpus")
               and ctx.files(".seclab/fuzz/corpus/record-long") == ["ok"],
               "both copies ran, wrote the run's corpus and left the persistent corpus alone")
    ctx.set(H="record-cli")

    # The gate's leak check: clean for this harness, a difference for a leaky one.
    ctx.sh('cp "$F"/runs/merge-$H-2/corpus/* "$F/corpus/$H/"')
    out = ctx.block("js-cli-leak-check", n=1, sample_size=8, timeout_s=120, **cli).stdout
    ctx.expect(clean_leak_check(out), "the leak check finds nothing in a clean harness")
    ctx.sh('mkdir -p "$F/corpus/leaky" && cp "$F/corpus/$H"/* "$F/corpus/leaky/"')
    ctx.set(H="leaky")
    out = ctx.block("js-cli-leak-check", n=1, sample_size=8, timeout_s=120, **dict(cli, target_file="fuzz/leaky.js")).stdout
    ctx.expect("### Different hit counts" in out and "fuzz/leaky.js" in out, "the leak check points at the leaking harness")

    # Detector gaps (harness.md's table): a command run through a shell and a path already resolved.
    ctx.set(H="tools")
    ctx.sh('printf "x;jaz_zer" > cmd-input && printf "../jaz_zer" > path-input')
    for n, target, name in ((1, "fuzz/greet.js", "cmd-input"), (2, "fuzz/read-data.js", "path-input")):
        ctx.block("js-cli-repro", n=n, timeout_s=60, finding=os.path.join(ctx.repo, name), target_file=target,
                  input_timeout_ms=5000, memory_mb=2048)
        ctx.expect_state("repro-tools-%d" % n, "finished")
    ctx.say("   ok: the built-in detectors miss both")

    # ES modules (harness.md): a named import of a built-in escapes the detectors, unless the
    # harness syncs the built-ins' exports and then imports the target.
    ctx.set(H="shell")
    esm = dict(timeout_s=60, finding=os.path.join(ctx.repo, "cmd-input"), input_timeout_ms=5000, memory_mb=2048)
    ctx.block("js-cli-repro", n=1, target_file="fuzz/shell-static.mjs", **esm)
    ctx.expect_state("repro-shell-1", "finished")
    ctx.say("   ok: the command detector misses a named import loaded statically")
    ctx.block("js-cli-repro", n=2, target_file="fuzz/shell.mjs", ok=(77,), **esm)
    ctx.expect("Command Injection" in ctx.log("repro-shell-2"), "the detector sees it after syncBuiltinESMExports()")
    ctx.sh("mkdir -p fuzz/corpus/shell && cp cmd-input fuzz/corpus/shell/")
    replay = ci_step("Replay the corpus", "fuzz/shell.mjs", "shell")
    ctx.expect(replay.returncode != 0 and "Command Injection" in replay.stdout + replay.stderr,
               "the JS CI template's replay applies the bug detectors")

    # Prototype pollution (triage.md) through an object-shaped input (harness.md): the report has no
    # stack; the tripwire names the write.
    ctx.set(H="merge")
    ctx.write_block("js-object-input", "fuzz/merge.js")
    ctx.sh("""printf '{"__proto__":{"polluted":"yes"}}' > pp-input""")
    pp = dict(timeout_s=60, finding=os.path.join(ctx.repo, "pp-input"), target_file="fuzz/merge.js", input_timeout_ms=5000)
    ctx.block("js-cli-repro", n=1, memory_mb=2048, ok=(77,), **pp)
    log = ctx.log("repro-merge-1")
    ctx.expect("Prototype Pollution" in log and "'polluted'" in log and "merge.js:" not in log,
               "the prototype pollution report names the property, not the line that wrote it")
    with open(os.path.join(ctx.repo, "src", "merge.js")) as handle:
        write = [i for i, text in enumerate(handle, 1) if "target[key] = value;" in text][0]
    ctx.block("js-pp-tripwire", n=1, prototype="Object", property="polluted", **pp)
    ctx.expect_state("pp-merge-1", "finished")
    ctx.expect("src/merge.js:%d:" % write in ctx.log("pp-merge-1"), "the tripwire prints the stack of the write")

    # Keys that shadow Object.prototype (harness.md): the check reports one the parser promised to drop.
    ctx.set(H="query")
    ctx.write_block("js-shadowing", "fuzz/query.js")
    query = dict(timeout_s=60, target_file="fuzz/query.js", input_timeout_ms=5000, memory_mb=2048)
    for n, text, ok in ((1, "a=1&b=2&__proto__=x", (0,)), (2, "a=1&toString=x", (77,))):
        with open(os.path.join(ctx.repo, "query-%d" % n), "w") as handle:
            handle.write(text)
        ctx.block("js-cli-repro", n=n, finding=os.path.join(ctx.repo, "query-%d" % n), ok=ok, **query)
    ctx.expect("Own key shadows Object.prototype" in ctx.log("repro-query-2") and "key: toString" in ctx.log("repro-query-2"),
               "the shadowing check reports an own toString, which no detector sees")
    cyclic = ('const p = require.resolve("./src/query"); const a = {}; a.self = a;'
              'require.cache[p] = { id: p, filename: p, loaded: true, exports: { parse: () => a } };'
              'require("./fuzz/query.js").fuzz(Buffer.from("x"));')
    ctx.sh('"%s/node" -e %s' % (node_bin(), shlex.quote(cyclic)))
    ctx.say("   ok: the shadowing check walks a cyclic result without overflowing its own stack")

    # An HTTP handler (harness.md), injected in process: the 500 is the finding.
    ctx.set(H="api")
    ctx.write_block("js-http", "fuzz/api.js")
    ctx.sh("""printf '{"fields":[1]}' > api-ok && printf '{}' > api-500""")
    api = dict(timeout_s=60, target_file="fuzz/api.js", input_timeout_ms=5000, memory_mb=2048)
    ctx.block("js-cli-repro", n=1, finding=os.path.join(ctx.repo, "api-ok"), **api)
    ctx.expect_state("repro-api-1", "finished")
    ctx.block("js-cli-repro", n=2, finding=os.path.join(ctx.repo, "api-500"), ok=(77,), **api)
    ctx.expect("Error: HTTP 500" in ctx.log("repro-api-2"), "the HTTP target reports the handler's 500")
    ctx.expect("SSRF" not in ctx.log("repro-api-2"), "the injected request opens no connection")

    # ReDoS: the catalog finds a regex literal, recheck flags it, and its repro times out through the harness.
    ctx.set(H="trim")
    with open(os.path.join(SKILLS, "shared", "security-threat-model", "references", "js.md")) as handle:
        pattern = re.search(r"## Regular expressions\n.*?```\n(.*?)\n```", handle.read(), re.S).group(1)
    with open(os.path.join(ctx.repo, "src", "tools.js")) as handle:
        sites = [i for i, text in enumerate(handle, 1) if re.search(pattern, text)]
    ctx.expect(len(sites) == 1, "the catalog's regex pattern finds the regex literal")
    os.makedirs(os.path.join(ctx.F, "sinks"), exist_ok=True)
    with open(os.path.join(ctx.F, "sinks", "trim.regex"), "w") as handle:
        handle.write("src/tools.js:%d\t\t[\\r\\n]+$\nsrc/tools.js:1\ti\t^[a-z]+$\n" % sites[0])
    out = ctx.block("redos-check", id="trim", input_timeout_s=1).stdout
    ctx.expect("polynomial 2" in out and "\tsafe" in out, "recheck flags the quadratic regex and passes the safe one")
    times = [int(t) for t in re.findall(r"chars (\d+) ms", out)]
    ctx.expect(len(times) == 3 and times[2] > 2.5 * times[1], "the times grow faster than the size")
    repro = re.search(r"repro: (\S+)", out).group(1)
    ctx.block("js-cli-repro", n=1, timeout_s=120, finding=repro, target_file="fuzz/trim.js", input_timeout_ms=1000,
              memory_mb=2048, ok=(70,))
    ctx.expect_state("repro-trim-1", "exited 70")
    buckets = ctx.sh('python3 "$F/bin/crash_buckets.py" "$F"/runs/repro-trim-1/copy-0/log').stdout
    ctx.expect("| timeout | - | - |" in buckets, "a Jazzer.js timeout has no stack, as triage.md says")
    out = ctx.block("js-hang-profile", n=1, timeout_s=120, finding=repro, target_file="fuzz/trim.js",
                    input_timeout_ms=1000).stdout
    ctx.expect("RegExp: [\\r\\n]+$" in out and "trimNewlines" in out, "the profile names the hanging regex and its function")


def build_fuzztest_toy(ctx: Context, variants=("fuzztest", "coverage")) -> None:
    """The fuzztest and coverage variants, from fuzz-c-cpp/references/fuzztest.md (Ninja and ccache when present)."""
    if shutil.which("ninja"):
        ctx.set(CMAKE_GENERATOR="Ninja")
    if shutil.which("ccache"):
        ctx.set(CMAKE_C_COMPILER_LAUNCHER="ccache", CMAKE_CXX_COMPILER_LAUNCHER="ccache")
    for variant in variants:
        ctx.block("fuzztest-build" if variant == "fuzztest" else "fuzztest-coverage-build", test_target="record_fuzz_test")


@scenario(
    "c-fuzztest",
    "fuzztest",
    ["c-env", "c-ub", "fuzztest-build", "fuzztest-coverage-build", "fuzztest-develop", "fuzztest-repro", "fuzztest-minimize", "triage-rebucket",
     "fuzztest-cov-replay", "fuzztest-cov-replay-files", "fuzztest-merge",
     "c-cov-export", "fuzztest-leak-check", "fuzztest-reach"],
)
def c_fuzztest(ctx: Context) -> None:
    ctx.set(H="record")
    linux = system() == "Linux"
    ctx.env_block("c-env", detect_leaks=1 if linux else 0, llvm_bin=llvm_bin())
    ctx.env_block("c-ub")
    build_fuzztest_toy(ctx)
    ctx.sh('mkdir -p "$F/corpus/$H"')
    # FuzzTest 2026-06-29 reads macOS's peak RSS (bytes) as KiB: no limit there (fuzztest.md).
    common = dict(test_binary="record_fuzz_test", fuzz_test="RecordTest.ParsesWithoutCrashing",
                  memory_mb=2048 if linux else 0, input_timeout_s=10)

    # A first round without a finding fills the flat corpus; then the planted bug.
    ctx.block("fuzztest-develop", round=1, run_s=120, **common)
    ctx.expect_state("r1-record", "found-crash")
    ctx.expect(len(ctx.files(".seclab/fuzz/corpus/record")) >= 1, "the round wrote inputs to the flat corpus")
    finding = ctx.findings("r1-record")[0]

    ctx.block("fuzztest-repro", n=1, timeout_s=120, finding=finding, ok=(1, 134), **common)
    ctx.expect("Assertion" in ctx.log("repro-record-1"), "the reproducer replays the assertion failure")
    buckets = ctx.sh('python3 "$F/bin/crash_buckets.py" "$F"/runs/repro-record-1/copy-0/log --root .').stdout
    ctx.expect("ParseRecord" in buckets, "crash_buckets.py names the project frame, not the fuzz test")
    ctx.block("fuzztest-minimize", n=1, bucket="assert", finding=finding, timeout_s=20, **common)
    minimized = os.path.join(ctx.F, "crashes", "assert", "minimized")
    ctx.expect(os.path.exists(os.path.join(ctx.F, "runs", "min-record-1-1")) and
               os.path.getsize(minimized) <= os.path.getsize(finding),
               "the loop ran and kept an input no larger (%d to %d bytes)" % (os.path.getsize(finding), os.path.getsize(minimized)))
    ctx.block("fuzztest-repro", n=2, timeout_s=120, finding=minimized, ok=(1, 134), **common)
    ctx.expect("Assertion" in ctx.log("repro-record-2"), "the minimized input reproduces")
    out = ctx.block("triage-rebucket", h="record", original=1, minimized=2).stdout
    ctx.expect("2 logs in 1 bucket" in out and "no crash detected" not in out,
               "the minimized input stays in the original's bucket")
    # The in-process repeat (fuzz-triage, step 2): a directory of copies as FUZZTEST_REPLAY, replayed in one process.
    copies = int(documented("fuzz-c-cpp", "triage.md", r"`FUZZTEST_REPLAY` set to a directory of (\d+) copies"))
    repeat = os.path.join(ctx.F, "crashes", "assert", "repeat")
    ctx.sh('mkdir -p "{0}" && for i in $(seq 1 {1}); do cp "$F/corpus/$H/{2}" "{0}/$i"; done'
           .format(repeat, copies, ctx.files(".seclab/fuzz/corpus/record")[0]))
    ctx.block("fuzztest-repro", n=3, timeout_s=120, finding=repeat, **common)
    ctx.expect_state("repro-record-3", "finished")
    ctx.expect("loaded %d inputs" % copies in ctx.log("repro-record-3"), "FuzzTest replays the %d copies in one process" % copies)

    # The coverage build keeps the assertions too: a replay of the crashing input aborts.
    ctx.set(H="assert-check")
    ctx.sh('mkdir -p "$F/corpus/$H" && cp "%s" "$F/corpus/$H/"' % finding)
    ctx.block("fuzztest-cov-replay", n=1, timeout_s=300, ok=(134,), **common)
    ctx.expect("Assertion" in ctx.log("cov-assert-check-1"), "the planted assert fires in the coverage build")
    # One file per process: the other inputs still count, and the log names the crashing one.
    ctx.sh('cp "$F/corpus/record"/* "$F/corpus/$H/"')
    ctx.block("fuzztest-cov-replay-files", n=2, timeout_s=300, **common)
    ctx.expect_state("cov-assert-check-2", "finished")
    ctx.expect("crashed: " in ctx.log("cov-assert-check-2") and ctx.files(".seclab/fuzz/runs/cov-assert-check-2/profraw"),
               "the per-file replay keeps the other inputs' coverage and names the crash")
    ctx.set(H="record")
    # Merging a long run's corpus: the union, minimized (here a stand-in for the run's corpus).
    ctx.sh('mkdir -p "$F/runs/record-long1/corpus" && cp "$F/corpus/$H"/* "$F/runs/record-long1/corpus/"')
    ctx.block("fuzztest-merge", n=1, timeout_s=300, long_run="record-long1", **common)
    ctx.expect_state("merge-record-1", "finished")
    merged = len(ctx.files(".seclab/fuzz/runs/merge-record-1/corpus"))
    ctx.expect(0 < merged <= len(ctx.files(".seclab/fuzz/corpus/record")),
               "the merge keeps a minimized corpus (%d files)" % merged)
    # A fuzz test seeded only through .WithSeeds keeps an empty corpus directory: the merge takes the run's inputs.
    ctx.set(H="seeded")
    ctx.sh('mkdir -p "$F/corpus/$H" "$F/runs/seeded-long1/corpus" && cp "$F/corpus/record"/* "$F/runs/seeded-long1/corpus/"')
    ctx.block("fuzztest-merge", n=1, timeout_s=300, long_run="seeded-long1", **common)
    ctx.expect_state("merge-seeded-1", "finished")
    ctx.set(H="record")
    ctx.block("fuzztest-cov-replay", n=1, timeout_s=300, **common)
    ctx.expect_state("cov-record-1", "finished")
    ctx.block("c-cov-export", n=1, round=1, binary="record_fuzz_test")
    ctx.expect_covered("$F/coverage/$H/r1.lcov", "src/record.cc")

    # The gate's leak check: clean for this fuzz test, a difference for a leaky one.
    leak = dict(common, sample_size=8, timeout_s=120)
    out = ctx.block("fuzztest-leak-check", n=1, **leak).stdout
    ctx.expect(clean_leak_check(out), "the leak check finds nothing in a clean fuzz test")
    ctx.sh('mkdir -p "$F/corpus/leaky" && cp "$F/corpus/$H"/* "$F/corpus/leaky/"')
    ctx.set(H="leaky")
    out = ctx.block("fuzztest-leak-check", n=1, **dict(leak, fuzz_test="RecordTest.ParsesLeaky")).stdout
    ctx.expect("### Different hit counts" in out and "record_fuzz_test.cc" in out, "the leak check points at the leaking fuzz test")

    # reach.py on FuzzTest: the chosen inputs go in through FUZZTEST_REPLAY={dir}.
    ctx.set(H="record")
    line = next(i for i, text in enumerate(open(os.path.join(ctx.repo, "src", "record.cc")), 1) if "name->assign" in text)
    out = ctx.block("fuzztest-reach", file="src/record.cc", line=line, timeout_s=120, **common).stdout
    ctx.expect("reached: " in out, "reach.py finds an input that reaches src/record.cc:%d" % line)


def check(blocks: dict) -> list:
    errors = []
    used = set()
    for scen in SCENARIOS.values():
        for name in scen.uses:
            used.add(name)
            if name not in blocks:
                errors.append("scenario %s uses block %r, which no doc marks" % (scen.name, name))
    for name, block in sorted(blocks.items()):
        if name not in used:
            errors.append("%s: block %r is marked but no scenario runs it" % (block.where(), name))
    return errors


def scenario_workdir(name: str) -> str:
    root = os.environ.get("DOCBLOCKS_WORKDIR")
    if not root:
        return tempfile.mkdtemp(prefix="docblocks-%s-" % name)
    work = os.path.join(os.path.abspath(root), name)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    return work


def run_scenario(name: str, keep: bool, verbose: bool) -> int:
    scen = SCENARIOS[name]
    if system() not in scen.systems:
        raise DocError("scenario %s runs on %s only, not %s (Linux: e.g. in CI's container image)"
                       % (name, " and ".join(scen.systems), system()))
    blocks = extract_blocks(doc_paths())
    missing = [name for name in scen.uses if name not in blocks]
    if missing:
        raise DocError("scenario %s uses blocks no doc marks: %s" % (name, ", ".join(missing)))
    work = scenario_workdir(name)
    print("scenario %s in %s" % (name, work), flush=True)
    try:
        ctx = Context(blocks, scen, work, verbose)
        scen.func(ctx)
        unused = [block for block in scen.uses if block not in ctx.blocks.used]
        if unused:
            raise DocError("scenario %s lists blocks it didn't run: %s" % (name, ", ".join(unused)))
        print("scenario %s passed" % name, flush=True)
        return 0
    finally:
        if keep:
            print("kept %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="check that markers and scenarios agree")
    sub.add_parser("list", help="list scenarios and their blocks")
    p = sub.add_parser("run", help="run one scenario")
    p.add_argument("scenario")
    p.add_argument("--keep", action="store_true", help="keep the work directory")
    p.add_argument("-v", "--verbose", action="store_true", help="print every command and its output")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            blocks = extract_blocks(doc_paths())
            errors = check(blocks) + unmarked_blocks(language_doc_paths())
            for error in errors:
                print("error: " + error, file=sys.stderr)
            print("%d blocks, %d scenarios, %d errors" % (len(blocks), len(SCENARIOS), len(errors)))
            return 1 if errors else 0
        if args.command == "list":
            blocks = extract_blocks(doc_paths())
            for scen in SCENARIOS.values():
                print("%s (%s; %s)" % (scen.name, scen.toy, ", ".join(scen.systems)))
                for name in scen.uses:
                    print("  %-22s %s" % (name, blocks[name].where() if name in blocks else "MISSING"))
            return 0
        if args.scenario not in SCENARIOS:
            print("error: no scenario %r (have: %s)" % (args.scenario, ", ".join(SCENARIOS)), file=sys.stderr)
            return 2
        return run_scenario(args.scenario, args.keep, args.verbose)
    except DocError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
