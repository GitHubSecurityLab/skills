#!/usr/bin/env python3
"""Find corpus inputs that reach a given source line.

The starting point for directed inputs: replay chosen subsets of a corpus in
the coverage build, read the line's hit count from the exported lcov, and
narrow down to the smallest input that reaches it.

    reach.py --harness <h> --corpus <dir> [--corpus <dir>]... --file F --line L
             --replay '<command>' --export '<command>' [--env K=V]... [--max 3]
             [--ok-exit 0] [--timeout <s>] [--skip-cpu-check]

Like launcher.py, it never decides what to run: the replay and export
commands come from the language skill. It fills these placeholders:

- replay: {dir} (a directory holding the chosen inputs; also in --env
  values, as FuzzTest's FUZZTEST_REPLAY={dir}), {files} (the chosen inputs
  as separate arguments) or {names} (Jest: their escaped names joined as
  n1|n2), and {out} (a fresh directory for raw coverage data, also in
  --env values);
- export: {out}, {lcov} (the lcov file to write) and {scratch} (the replay
  step's working directory, for coverage.py --sf-base). It reads files
  only, so it runs in this shell's environment, not with --env: the
  shell's PATH finds llvm-profdata, java and python3.

Each replay runs as a self-ending launcher run (reach-<h>-<n>-<step>);
findings it records (an input that crashes) stay in its artifacts/ and are
printed. Python 3.9+ standard library only.
"""

import argparse
import contextlib
import filecmp
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

EXIT_USAGE = 2
EXIT_INVALID = 3
EXIT_NOT_REACHED = 1

# Linux limits one argument to 128 KiB (MAX_ARG_STRLEN); stay below it and
# below a share of the total argument space.
ARG_LIMIT = 100 * 1024
TOTAL_ARG_SHARE = 0.25

HERE = os.path.dirname(os.path.abspath(__file__))


class ReachError(Exception):
    def __init__(self, message: str, code: int = EXIT_INVALID) -> None:
        super().__init__(message)
        self.code = code


def say(text: str, stream=None) -> None:
    print(text, file=stream or sys.stdout, flush=True)


# --------------------------------------------------------------------------
# Workspace and launcher


def default_workspace() -> str:
    if os.path.basename(HERE) == "bin":
        return os.path.dirname(HERE)
    return os.path.abspath(os.path.join(".seclab", "fuzz"))


def default_root(workspace: str) -> str:
    parts = os.path.abspath(workspace).split(os.sep)
    if len(parts) >= 2 and parts[-2:] == [".seclab", "fuzz"]:
        return os.sep.join(parts[:-2]) or os.sep
    return os.path.abspath(".")


_LAUNCHER = []


def launcher():
    """launcher.py from the same directory, loaded once and run in-process."""
    if not _LAUNCHER:
        path = os.path.join(HERE, "launcher.py")
        if not os.path.exists(path):
            raise ReachError("launcher.py not found next to reach.py (%s)" % HERE, EXIT_USAGE)
        spec = importlib.util.spec_from_file_location("seclab_launcher", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _LAUNCHER.append(module)
    return _LAUNCHER[0]


def run_launcher(argv: list) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = launcher().main(argv)
    return code, out.getvalue(), err.getvalue()


def next_search_id(workspace: str, harness: str) -> str:
    runs = os.path.join(workspace, "runs")
    taken = set(os.listdir(runs)) if os.path.isdir(runs) else set()
    pattern = re.compile(r"^reach-%s-(\d+)(-|$)" % re.escape(harness))
    numbers = [int(m.group(1)) for m in (pattern.match(name) for name in taken) if m]
    return "reach-%s-%d" % (harness, max(numbers, default=0) + 1)


# --------------------------------------------------------------------------
# Candidates


def collect_candidates(corpora: list, need_unique_names: bool) -> list:
    """Regular, non-empty, non-hidden files at the top of each corpus
    directory, smallest first. Zero-byte inputs are left out: a libFuzzer
    directory merge skips them, and they're no useful starting point."""
    seen: dict = {}
    out = []
    for corpus in corpora:
        corpus = os.path.abspath(corpus)  # {files} reach a command that runs in its scratch directory
        if not os.path.isdir(corpus):
            raise ReachError("no such corpus directory: %s" % corpus, EXIT_USAGE)
        for name in sorted(os.listdir(corpus)):
            path = os.path.join(corpus, name)
            if name.startswith(".") or not os.path.isfile(path) or os.path.getsize(path) == 0:
                continue
            if need_unique_names and name in seen:
                if filecmp.cmp(seen[name], path, shallow=False):
                    continue  # same content under the same name: keep it once
                raise ReachError(
                    "%s and %s have the same name but different contents; Jest selects inputs by name. "
                    "Use a temporary probe at the line instead (see the language skill)." % (seen[name], path)
                )
            seen.setdefault(name, path)
            out.append(path)
    out.sort(key=lambda p: (os.path.getsize(p), p))
    return out


# --------------------------------------------------------------------------
# lcov


def line_hits(lcov_path: str, target: str, target_line: int, root: str):
    """(hits at the target line, the file's instrumented lines), or None when
    the report has no record for the file. Hits are the line's DA count: a
    `switch` case line has branches that run when the case doesn't. Only a
    line without DA counts its best BRDA count. Hits are None when the file
    has no record for the line."""
    found = False
    da = brda = None
    lines = set()
    current = None
    with open(lcov_path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("SF:"):
                current = same_file(line[3:], target, root)
                found = found or current
            elif line == "end_of_record":
                current = None
            elif current and line.startswith("DA:"):
                parts = line[3:].split(",")
                if len(parts) >= 2:
                    lines.add(_int(parts[0]))
                    if parts[0] == str(target_line):
                        da = max(da or 0, _int(parts[1]))
            elif current and line.startswith("BRDA:"):
                parts = line[5:].split(",")
                if len(parts) == 4:
                    lines.add(_int(parts[0]))
                    if parts[0] == str(target_line):
                        brda = max(brda or 0, 0 if parts[3] in ("-", "") else _int(parts[3]))
    if not found:
        return None
    return (da if da is not None else brda), sorted(lines)


def _int(text: str) -> int:
    try:
        return int(float(text))
    except ValueError:
        return 0


def same_file(sf: str, target: str, root: str) -> bool:
    sf = sf.replace("\\", "/")
    if os.path.isabs(sf):
        for base in {os.path.abspath(root), os.path.realpath(root)}:
            if os.path.normpath(sf).startswith(base.rstrip("/") + "/"):
                sf = os.path.relpath(os.path.normpath(sf), base)
                break
    return os.path.normpath(sf) == os.path.normpath(target)


# --------------------------------------------------------------------------
# Steps


def js_escape(name: str) -> str:
    return re.sub(r"([.*+?^${}()|\[\]\\/-])", r"\\\1", name)


class Searcher:
    def __init__(self, args, workspace: str, root: str, search_id: str) -> None:
        self.args = args
        self.workspace = workspace
        self.root = root
        self.search_id = search_id
        self.replay = shlex.split(args.replay)
        self.export = shlex.split(args.export)
        self.ok = {int(c) for c in args.ok_exit.split(",") if c.strip()}
        self.mode = self.input_mode()
        self.step = 0
        self.file_seen_in_report = False
        self.line_seen_in_report = False
        self.file_lines: list = []
        self.reported: set = set()  # findings already printed, by content
        self.exit_noted = False

    def input_mode(self) -> str:
        text = " ".join(self.replay + (self.args.env or []))  # FuzzTest: FUZZTEST_REPLAY={dir}
        modes = [m for m in ("{dir}", "{files}", "{names}") if m in text]
        if len(modes) != 1:
            raise ReachError("--replay (or an --env value) must contain exactly one of {dir}, {files}, {names}",
                             EXIT_USAGE)
        return modes[0][1:-1]

    def batches(self, inputs: list) -> list:
        if self.mode == "dir":
            return [inputs]
        total_limit = int((os.sysconf("SC_ARG_MAX") if hasattr(os, "sysconf") else 262144) * TOTAL_ARG_SHARE)
        out, current, size = [], [], 0
        for path in inputs:
            item = len(js_escape(os.path.basename(path))) + 1 if self.mode == "names" else len(path) + 1
            limit = ARG_LIMIT if self.mode == "names" else total_limit
            if current and size + item > limit:
                out.append(current)
                current, size = [], 0
            current.append(path)
            size += item
        if current:
            out.append(current)
        return out

    def run_step(self, inputs: list):
        """Replay `inputs` (in batches, each its own process, up to the first
        that reaches the line); return (reached, hits) or raise on an invalid step."""
        for batch in self.batches(inputs):
            hits = self.run_batch(batch)
            if hits:
                return True, hits
        return False, 0

    def run_batch(self, batch: list) -> int:
        self.step += 1
        run_id = "%s-%d" % (self.search_id, self.step)
        rdir = os.path.join(self.workspace, "runs", run_id)
        out = os.path.join(rdir, "out")
        chosen = os.path.join(rdir, "inputs")
        lcov = os.path.join(rdir, "coverage.lcov")
        os.makedirs(out, exist_ok=True)
        values = {"out": out, "lcov": lcov}
        if self.mode == "dir":
            os.makedirs(chosen, exist_ok=True)
            for index, path in enumerate(batch):
                # copies, not links (engines may skip links), named
                # uniquely since corpora can share file names
                shutil.copyfile(path, os.path.join(chosen, "%06d-%s" % (index, os.path.basename(path))))
            values["dir"] = chosen
        argv = []
        for arg in self.replay:
            if arg == "{files}":
                argv.extend(batch)
                continue
            if "{names}" in arg:
                arg = arg.replace("{names}", "|".join(js_escape(os.path.basename(p)) for p in batch))
            argv.append(fill(arg, values))
        cmd = ["--workspace", self.workspace, "start", "--id", run_id,
               "--harness", self.args.harness, "--timeout", str(self.args.timeout)]
        if self.args.skip_cpu_check:
            cmd.append("--skip-cpu-check")
        for item in self.args.env or []:
            cmd += ["--env", fill(item, values)]
        cmd += ["--"] + argv
        _code, stdout, stderr = run_launcher(cmd)
        meta = read_meta(rdir)
        if not meta.get("copies"):
            # the launcher refused to start (usage error or CPU check)
            raise ReachError("launcher: " + (stderr.strip() or stdout.strip()), EXIT_USAGE)
        copy = (meta.get("copies") or [{}])[0]
        state = copy.get("state", "")
        code = copy.get("returncode")
        if state == "stopped":
            raise ReachError("step %s was stopped (Ctrl-C or launcher.py stop): the search ends here" % run_id)
        if state == "timed out" or code is None or code not in self.ok:
            raise ReachError(
                "step %s is invalid (%s, exit %s; allowed %s); its log: %s%s"
                % (run_id, state or "no state", code, ",".join(map(str, sorted(self.ok))),
                   os.path.join(rdir, "copy-0", "log"), self.crash_hint(rdir, copy, batch))
            )
        self.report(rdir, copy, batch)
        scratch = os.path.realpath(os.path.join(rdir, "copy-0", "work"))
        export = [fill(arg, dict(values, scratch=scratch)) for arg in self.export]
        try:
            exported = subprocess.run(export, capture_output=True, text=True, cwd=self.root, timeout=self.args.timeout)
        except subprocess.TimeoutExpired:
            raise ReachError("step %s: the export took longer than %d s" % (run_id, self.args.timeout))
        if exported.returncode != 0 or not os.path.exists(lcov) or os.path.getsize(lcov) == 0:
            raise ReachError("step %s: the export wrote no lcov (%s)" % (run_id, (exported.stderr or "").strip()[-300:]))
        result = line_hits(lcov, self.args.file, self.args.line, self.root)
        # Istanbul lists only the files a replay loaded: inputs that never
        # load the module leave it out. search() checks that the first step,
        # which runs every input, listed the file and the line.
        hits = 0
        if result is not None:
            self.file_seen_in_report = True
            self.file_lines = result[1]
            if result[0] is not None:
                self.line_seen_in_report = True
                hits = result[0]
        # A valid step keeps only its log and meta.json: copied inputs and raw
        # coverage would add up to several corpus copies per search.
        shutil.rmtree(chosen, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
        os.remove(lcov)
        return hits

    def crash_hint(self, rdir: str, copy: dict, batch: list) -> str:
        findings = copy.get("new_findings") or []
        if not findings:
            return ""
        crashed = os.path.join(rdir, findings[0]["path"])
        source = crashed_input(crashed, batch)
        return "; the input %s crashed" % source if source else "; a finding was written: %s" % crashed

    def report(self, rdir: str, copy: dict, batch: list) -> None:
        """A valid step can have findings too (libFuzzer's -merge=1 goes on
        past a crashing input), or an allowed failing exit (Jest, and Jazzer
        with --keep_going=1, after an input throws): say so once, for
        fuzz-triage."""
        for finding in copy.get("new_findings") or []:
            path = os.path.join(rdir, finding["path"])
            try:
                with open(path, "rb") as handle:
                    key = hashlib.sha1(handle.read()).hexdigest()
            except OSError:
                key = path
            if key in self.reported:
                continue
            self.reported.add(key)
            source = crashed_input(path, batch)
            say("finding: %s: %s; a bug for fuzz-triage"
                % (path, "the input %s crashed" % source if source else "an input crashed"))
        code = copy.get("returncode")
        if code and not self.exit_noted:
            self.exit_noted = True
            say("note: a replay exited %s (allowed by --ok-exit): an input failed; its log: %s"
                % (code, os.path.join(rdir, "copy-0", "log")))

    def search(self, candidates: list) -> list:
        """Up to --max smallest inputs that reach the line on their own."""
        found = []
        remaining = list(candidates)
        first = True
        while remaining and len(found) < self.args.max:
            reached, _ = self.run_step(remaining)
            if first and not self.file_seen_in_report:
                raise ReachError(
                    "no step's report has a record for %s; check --file (from the repository root) "
                    "and the export's paths" % self.args.file
                )
            if first and not self.line_seen_in_report:
                below = [n for n in self.file_lines if n < self.args.line][-3:]
                above = [n for n in self.file_lines if n > self.args.line][:3]
                raise ReachError(
                    "%s:%d has no line record in the coverage (not executable code, or the wrong line); "
                    "instrumented lines nearby: %s"
                    % (self.args.file, self.args.line, ", ".join(map(str, below + above)) or "none")
                )
            if not reached:
                if first:
                    say("not reached: no input of %d reaches %s:%d" % (len(candidates), self.args.file, self.args.line))
                break
            first = False
            pool = remaining
            while len(pool) > 1:
                half, rest = pool[: len(pool) // 2], pool[len(pool) // 2 :]
                if self.run_step(half)[0]:
                    pool = half
                elif self.run_step(rest)[0]:
                    pool = rest
                else:
                    say("reached only after other inputs: no single input of these %d reaches %s:%d (state "
                        "from an earlier input in the same process)" % (len(pool), self.args.file, self.args.line))
                    return found
            chosen = pool[0]
            alone, hits = self.run_step([chosen])
            if alone:
                say("reached: %s (%d bytes, %d hits)" % (chosen, os.path.getsize(chosen), hits))
                found.append(chosen)
            else:
                say("reached only after other inputs: %s doesn't reach the line alone (state from an "
                    "earlier input in the same process)" % chosen)
            remaining = remaining[remaining.index(chosen) + 1 :]
        return found


def crashed_input(finding: str, batch: list):
    """The input in `batch` that the finding file is a copy of, or None."""
    for path in batch:
        try:
            if filecmp.cmp(path, finding, shallow=False):
                return path
        except OSError:
            continue
    return None


def fill(text: str, values: dict) -> str:
    for key, value in values.items():
        text = text.replace("{%s}" % key, value)
    return text


def read_meta(rdir: str) -> dict:
    try:
        with open(os.path.join(rdir, "meta.json")) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find corpus inputs that reach a source line.")
    parser.add_argument("--workspace", help="the .seclab/fuzz directory (default: found from the script's location)")
    parser.add_argument("--root", help="repository root for --file (default: above .seclab/fuzz)")
    parser.add_argument("--harness", required=True)
    parser.add_argument("--corpus", action="append", required=True, help="corpus directory (repeatable)")
    parser.add_argument("--file", required=True, help="source file, relative to the repository root")
    parser.add_argument("--line", type=int, required=True)
    parser.add_argument("--replay", required=True, help="replay command with {dir}, {files} or {names}, and {out}")
    parser.add_argument("--export", required=True, help="export command writing {lcov} (may use {out}, {scratch})")
    parser.add_argument("--env", action="append", help="K=V for the replay (repeatable; {out} and {dir} are filled)")
    parser.add_argument("--max", type=int, default=3, help="inputs to report (default 3)")
    parser.add_argument("--ok-exit", default="0", help="exit codes of a valid replay, e.g. 0,1 for Jest")
    parser.add_argument("--timeout", type=int, default=600, help="kill limit per step, and for each export, in seconds (default 600)")
    parser.add_argument("--skip-cpu-check", action="store_true", help="only with the user's OK")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    workspace = os.path.abspath(args.workspace or default_workspace())
    root = os.path.abspath(args.root or default_root(workspace))
    try:
        searcher = Searcher(args, workspace, root, next_search_id(workspace, args.harness))
        candidates = collect_candidates(args.corpus, searcher.mode == "names")
        if not candidates:
            say("no candidates: the corpus directories hold no non-empty inputs")
            return EXIT_NOT_REACHED
        say("%s: %d candidates, smallest first" % (searcher.search_id, len(candidates)))
        found = searcher.search(candidates)
        say("%d step%s" % (searcher.step, "" if searcher.step == 1 else "s"))
        return 0 if found else EXIT_NOT_REACHED
    except ReachError as error:
        say("error: %s" % error, sys.stderr)
        return error.code


if __name__ == "__main__":
    # The replays' parent is this process: keep only what the launcher keeps.
    launcher().reexec_scrubbed(__file__, sys.argv[1:])
    sys.exit(main())
