#!/usr/bin/env python3
"""Coverage helper for fuzzing campaigns.

Reads lcov tracefiles (from llvm-cov, from Jazzer.js/Istanbul, or converted
from JaCoCo XML) and prints compact Markdown that an agent can act on.

Commands:
  from-jacoco   convert a JaCoCo XML report to lcov (tagged as JaCoCo-derived:
                its branch identities are invented per line, so several such
                files are never combined; merge the .exec files instead)
  merge         combine several lcov files
  summary       branch/line/function coverage, optionally limited to scope
                files and compared with a previous snapshot
  gaps          functions with branches never taken, ranked
  show          source of one file annotated with hit counts (one function,
                or a line range for functions the data doesn't name)
  overlap       branches and lines only one of two snapshots covers; --counts compares hit counts
                (JaCoCo-derived input needs --union, the merged report)
  sites         reached / not reached for each sink line in a TSV file

Standard library only, Python 3.9+. Reads files; never runs target code.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import os
import re
import shutil
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Data model


@dataclass
class FileCov:
    path: str
    lines: dict = field(default_factory=dict)  # line -> hit count
    # (line, block, branch) -> hit count, or None when the branch's
    # condition was never evaluated ("-" in lcov)
    branches: dict = field(default_factory=dict)
    functions: dict = field(default_factory=dict)  # name -> [start, end, hits]
    # Totals from BRF/BRH/LF/LH/FNF/FNH, used only when detail is missing.
    fallback: dict = field(default_factory=dict)
    # "jacoco" for data converted from JaCoCo XML: its branch identities are
    # invented per line (JaCoCo reports only counts), so only counts per
    # line are meaningful.
    origin: str = ""
    def add_line(self, line: int, hits: int) -> None:
        self.lines[line] = self.lines.get(line, 0) + hits

    def add_branch(self, key: tuple, hits) -> None:
        if key in self.branches:
            old = self.branches[key]
            if old is None and hits is None:
                return
            self.branches[key] = (old or 0) + (hits or 0)
        else:
            self.branches[key] = hits

    def add_function(self, name: str, start, end, hits: int) -> None:
        entry = self.functions.get(name)
        if entry is None:
            self.functions[name] = [start, end, hits]
            return
        if start is not None and (entry[0] is None or start < entry[0]):
            entry[0] = start
        if end is not None and (entry[1] is None or end > entry[1]):
            entry[1] = end
        entry[2] += hits

    def merge(self, other: FileCov) -> None:
        for line, hits in other.lines.items():
            self.add_line(line, hits)
        for key, hits in other.branches.items():
            self.add_branch(key, hits)
        for name, (start, end, hits) in other.functions.items():
            self.add_function(name, start, end, hits)
        for key, value in other.fallback.items():
            self.fallback[key] = self.fallback.get(key, 0) + value
        self.origin = self.origin or other.origin

    # Totals -------------------------------------------------------------

    def branch_counts(self) -> tuple:
        if self.branches:
            hit = sum(1 for v in self.branches.values() if v)
            return hit, len(self.branches)
        return self.fallback.get("BRH", 0), self.fallback.get("BRF", 0)

    def line_counts(self) -> tuple:
        if self.lines:
            hit = sum(1 for v in self.lines.values() if v > 0)
            return hit, len(self.lines)
        return self.fallback.get("LH", 0), self.fallback.get("LF", 0)

    def function_counts(self) -> tuple:
        if self.functions:
            if self.origin != "jacoco" and NATIVE_SOURCE.search(self.path):
                # llvm lists template instantiations, and a static inline
                # function per translation unit, all at one start line.
                spans: dict = {}
                for name, (start, _end, hits) in self.functions.items():
                    key = name if start is None else start
                    spans[key] = spans.get(key, 0) + hits
                return sum(1 for hits in spans.values() if hits > 0), len(spans)
            hit = sum(1 for v in self.functions.values() if v[2] > 0)
            return hit, len(self.functions)
        return self.fallback.get("FNH", 0), self.fallback.get("FNF", 0)


NATIVE_SOURCE = re.compile(r"\.(c|cc|cpp|cxx|c\+\+|h|hh|hpp|hxx|h\+\+|inc|inl|ipp|tcc|m|mm)$", re.I)


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return int(float(text))


def _branch_sort_key(key: tuple) -> tuple:
    line, block, branch = key

    def part(value):
        return (0, int(value), "") if str(value).isdigit() else (1, 0, str(value))

    return (line, part(block), part(branch))


# --------------------------------------------------------------------------
# lcov reading and writing


def parse_lcov(path: str) -> dict:
    """Parse an lcov tracefile into {source path: FileCov}."""
    files: dict = {}
    current = None
    test_name = ""
    fnl: dict = {}  # lcov 2.x FNL/FNA index -> (start, end)
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line == "end_of_record":
                current = None
                fnl = {}
                test_name = ""
                continue
            tag, sep, value = line.partition(":")
            if not sep:
                continue
            if tag == "TN":
                test_name = value
                continue
            if tag == "SF":
                current = files.setdefault(value, FileCov(value))
                if test_name.startswith(JACOCO_TN):
                    current.origin = "jacoco"
                continue
            if current is None:
                continue
            try:
                _parse_record(current, tag, value, fnl)
            except (ValueError, IndexError):
                # Skip malformed records rather than failing the whole file.
                continue
    return files


def _parse_record(cov: FileCov, tag: str, value: str, fnl: dict) -> None:
    if tag == "DA":
        parts = value.split(",")
        cov.add_line(_int(parts[0]), _int(parts[1]))
    elif tag == "BRDA":
        line, block, branch, taken = value.split(",", 3)
        hits = None if taken.strip() == "-" else _int(taken)
        cov.add_branch((_int(line), block, branch), hits)
    elif tag == "FN":
        parts = value.split(",")
        if len(parts) >= 3 and parts[1].isdigit():
            start, end, name = _int(parts[0]), _int(parts[1]), ",".join(parts[2:])
        else:
            start, end, name = _int(parts[0]), None, ",".join(parts[1:])
        cov.add_function(name, start, end, 0)
    elif tag == "FNDA":
        hits, name = value.split(",", 1)
        cov.add_function(name, None, None, _int(hits))
    elif tag == "FNL":
        parts = value.split(",")
        end = _int(parts[2]) if len(parts) > 2 else None
        fnl[parts[0]] = (_int(parts[1]), end)
    elif tag == "FNA":
        index, hits, name = value.split(",", 2)
        start, end = fnl.get(index, (None, None))
        cov.add_function(name, start, end, _int(hits))
    elif tag in ("BRF", "BRH", "LF", "LH", "FNF", "FNH"):
        cov.fallback[tag] = cov.fallback.get(tag, 0) + _int(value)


class UsageError(Exception):
    """A request the data can't answer correctly; reported without a traceback."""


JACOCO_TN = "jacoco-derived"


def is_jacoco(files: dict) -> bool:
    return any(cov.origin == "jacoco" for cov in files.values())


def load_many(paths: list, sf_base=None) -> dict:
    """Parse and merge lcov files. With sf_base, relative SF: paths (Istanbul
    writes them relative to the replay's working directory) are resolved
    against it, textually, so this works after the directory is deleted."""
    merged: dict = {}
    jacoco_inputs = []
    base = _real(sf_base) if sf_base else None
    for path in paths:
        files = parse_lcov(path)
        if is_jacoco(files):
            jacoco_inputs.append(path)
        for name, cov in files.items():
            if base and not os.path.isabs(name):
                name = _norm(os.path.join(base, name))
                cov.path = name
            if name in merged:
                merged[name].merge(cov)
            else:
                merged[name] = cov
    if len(jacoco_inputs) > 1:
        raise UsageError(
            "several JaCoCo-derived lcov files (%s): JaCoCo reports only counts per "
            "line, so combining them double-counts or misses branches. Merge the "
            ".exec files with `jacococli merge` and convert the merged report instead."
            % ", ".join(jacoco_inputs)
        )
    return merged


def write_lcov(files: dict, out) -> None:
    for name in sorted(files):
        cov = files[name]
        out.write("TN:%s\n" % (JACOCO_TN if cov.origin == "jacoco" else ""))
        out.write("SF:%s\n" % name)
        fns = sorted(
            cov.functions.items(),
            key=lambda item: (item[1][0] if item[1][0] is not None else 0, item[0]),
        )
        for fname, (start, end, _hits) in fns:
            if start is None:
                continue
            if end is not None:
                out.write("FN:%d,%d,%s\n" % (start, end, fname))
            else:
                out.write("FN:%d,%s\n" % (start, fname))
        for fname, (_start, _end, hits) in fns:
            out.write("FNDA:%d,%s\n" % (hits, fname))
        fn_hit, fn_total = cov.function_counts()
        out.write("FNF:%d\nFNH:%d\n" % (fn_total, fn_hit))
        for line in sorted(cov.lines):
            out.write("DA:%d,%d\n" % (line, cov.lines[line]))
        for key in sorted(cov.branches, key=_branch_sort_key):
            hits = cov.branches[key]
            taken = "-" if hits is None else str(hits)
            out.write("BRDA:%d,%s,%s,%s\n" % (key[0], key[1], key[2], taken))
        br_hit, br_total = cov.branch_counts()
        out.write("BRF:%d\nBRH:%d\n" % (br_total, br_hit))
        ln_hit, ln_total = cov.line_counts()
        out.write("LF:%d\nLH:%d\n" % (ln_total, ln_hit))
        out.write("end_of_record\n")


# --------------------------------------------------------------------------
# Paths and scope


def _norm(path: str) -> str:
    return os.path.normpath(path.replace("\\", "/")).replace("\\", "/")


def _real(path: str) -> str:
    return _norm(os.path.realpath(path))


class PathMapper:
    """Maps lcov source paths to display paths relative to a root."""

    def __init__(self, root) -> None:
        self.roots = []
        if root:
            self.roots = [_norm(os.path.abspath(root)), _real(root)]

    def display(self, path: str) -> str:
        norm = _norm(path)
        for root in self.roots:
            if norm == root:
                return "."
            if norm.startswith(root + "/"):
                return norm[len(root) + 1 :]
        if os.path.isabs(norm):
            real = _real(norm)
            for root in self.roots:
                if real.startswith(root + "/"):
                    return real[len(root) + 1 :]
        return norm


def load_scope(paths) -> list:
    entries = []
    for path in paths or []:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                entry = raw.split("#", 1)[0].strip()
                if entry and entry not in entries:
                    entries.append(entry)
    return entries


def _entry_matches(display: str, entry: str) -> bool:
    """Scope entries are paths relative to the repository root: a file, or a
    directory ending in '/'. They never match by suffix, so `src/parse.c`
    doesn't also select `tests/src/parse.c`."""
    is_dir = entry.endswith("/")
    entry = _norm(entry)
    if entry.startswith("./"):
        entry = entry[2:]
    if entry in (".", ""):
        return True
    if is_dir:
        return display.startswith(entry + "/")
    return display == entry


def _suffix_matches(display: str, entry: str) -> bool:
    """Looser match for naming one file on the command line (`show --file`)."""
    entry = _norm(entry)
    if entry.startswith("./"):
        entry = entry[2:]
    return display == entry or display.endswith("/" + entry)


def filter_scope(files: dict, mapper: PathMapper, scope: list) -> tuple:
    """Return ({display: FileCov} in scope, [scope entries with no data])."""
    shown = {}
    for name, cov in files.items():
        display = mapper.display(name)
        if display in shown:
            shown[display].merge(cov)
        else:
            copy = FileCov(display)
            copy.merge(cov)
            shown[display] = copy
    if not scope:
        return shown, []
    selected = {}
    unmatched = []
    for entry in scope:
        hits = [d for d in shown if _entry_matches(d, entry)]
        if not hits:
            unmatched.append(entry)
        for display in hits:
            selected[display] = shown[display]
    return selected, unmatched


# --------------------------------------------------------------------------
# Formatting helpers


def _pct(hit: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return "%.2f%%" % (100.0 * hit / total)


def _ratio(hit: int, total: int) -> str:
    return "%d/%d (%s)" % (hit, total, _pct(hit, total))


def _points(hit: int, total: int, prev_hit: int, prev_total: int):
    if total == 0 or prev_total == 0:
        return None
    return 100.0 * hit / total - 100.0 * prev_hit / prev_total


def _totals(files: dict) -> dict:
    totals = {"br": [0, 0], "ln": [0, 0], "fn": [0, 0]}
    for cov in files.values():
        for key, counts in (
            ("br", cov.branch_counts()),
            ("ln", cov.line_counts()),
            ("fn", cov.function_counts()),
        ):
            totals[key][0] += counts[0]
            totals[key][1] += counts[1]
    return totals


def _ranges(numbers: list) -> str:
    numbers = sorted(set(numbers))
    if not numbers:
        return ""
    parts = []
    start = prev = numbers[0]
    for number in numbers[1:]:
        if number == prev + 1:
            prev = number
            continue
        parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
        start = prev = number
    parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ", ".join(parts)


def _demangler():
    for tool in ("llvm-cxxfilt", "c++filt"):
        found = shutil.which(tool)
        if found:
            return found
    return None


# llvm-cov names functions with internal linkage (static) "<file>:<name>".
FILE_PREFIX = re.compile(r"^[^:\s]+\.\w+:(?!:)")


def demangle(names: list) -> dict:
    """Best-effort demangling of C++ names, without llvm-cov's file prefix;
    returns {name: readable}."""
    bare = {n: FILE_PREFIX.sub("", n, count=1) for n in names}
    mangled = sorted({b for b in bare.values() if b.startswith(("_Z", "__Z"))})
    result = dict(bare)
    tool = _demangler()
    if not mangled or not tool:
        return result
    try:
        proc = subprocess.run(
            [tool],
            input="\n".join(mangled) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return result
    out = proc.stdout.splitlines()
    if len(out) == len(mangled):
        readable = {name: line.strip() or name for name, line in zip(mangled, out)}
        result = {n: readable.get(b, b) for n, b in bare.items()}
    return result


# --------------------------------------------------------------------------
# Commands


def _load(args, paths: list) -> dict:
    return load_many(paths, getattr(args, "sf_base", None))


def cmd_merge(args) -> int:
    files = _load(args, args.lcov)
    if args.root:
        mapper = PathMapper(args.root)
        renamed: dict = {}
        for name, cov in files.items():
            name = mapper.display(name)
            cov.path = name
            if name in renamed:
                renamed[name].merge(cov)
            else:
                renamed[name] = cov
        files = renamed
    if args.output:
        with open(args.output, "w", encoding="utf-8") as out:
            write_lcov(files, out)
    else:
        write_lcov(files, sys.stdout)
    return 0


def cmd_summary(args) -> int:
    mapper = PathMapper(args.root)
    scope = load_scope(args.scope)
    files, unmatched = filter_scope(_load(args, args.lcov), mapper, scope)
    prev_files = {}
    if args.prev:
        prev_files, _ = filter_scope(_load(args, [args.prev]), mapper, scope)

    totals = _totals(files)
    prev_totals = _totals(prev_files) if args.prev else None
    label = "%d files in scope" % len(files) if scope else "%d files" % len(files)
    if unmatched:
        label += "; incomplete"
    print("## Coverage summary (%s)" % label)
    print()
    if unmatched:
        print(
            "**Incomplete:** %d scope %s no coverage data (not compiled, not loaded, "
            "or not under --root), so the totals below leave %s out: %s"
            % (
                len(unmatched),
                "entry has" if len(unmatched) == 1 else "entries have",
                "it" if len(unmatched) == 1 else "them",
                ", ".join(unmatched),
            )
        )
        print()
        print(
            "warning: %d scope entries have no coverage data: %s"
            % (len(unmatched), ", ".join(unmatched)),
            file=sys.stderr,
        )
    for key, name in (("br", "Branches"), ("ln", "Lines"), ("fn", "Functions")):
        hit, total = totals[key]
        text = "- **%s:** %s" % (name, _ratio(hit, total))
        if prev_totals is not None:
            p_hit, p_total = prev_totals[key]
            delta = _points(hit, total, p_hit, p_total)
            text += "; previous %s" % _ratio(p_hit, p_total)
            if delta is not None:
                text += "; change %+.2f points (%+d covered)" % (delta, hit - p_hit)
        print(text)
    rows = []
    for display, cov in files.items():
        br_hit, br_total = cov.branch_counts()
        ln_hit, ln_total = cov.line_counts()
        fn_hit, fn_total = cov.function_counts()
        change = ""
        if args.prev:
            prev = prev_files.get(display)
            if prev is None:
                change = "new"
            else:
                p_hit, p_total = prev.branch_counts()
                delta = _points(br_hit, br_total, p_hit, p_total)
                change = "%+.2f" % delta if delta is not None else ""
        rows.append(
            (
                br_total - br_hit,
                display,
                _ratio(br_hit, br_total),
                change,
                _ratio(ln_hit, ln_total),
                "%d/%d" % (fn_hit, fn_total),
            )
        )
    rows.sort(key=lambda row: (-row[0], row[1]))
    print()
    if args.prev:
        print("| File | Branches | Change (points) | Lines | Functions |")
        print("|---|---|---|---|---|")
    else:
        print("| File | Branches | Lines | Functions |")
        print("|---|---|---|---|")
    for row in rows[: args.top]:
        if args.prev:
            print("| %s | %s | %s | %s | %s |" % (row[1], row[2], row[3], row[4], row[5]))
        else:
            print("| %s | %s | %s | %s |" % (row[1], row[2], row[4], row[5]))
    if len(rows) > args.top:
        print()
        print("(%d more files not shown; use --top)" % (len(rows) - args.top))
    return 0


# Functions a compiler or coverage tool names for a callback or lambda,
# often inside another function: Istanbul's "(anonymous_<n>)" (also class
# and object methods), JaCoCo's "lambda$<method>$<n>", clang's "$_<n>" and
# "Ul...E_" (mangled lambdas).
SYNTHETIC = re.compile(r"\(anonymous_\d+\)|lambda\$|\$_\d+|Ul\w*?E\d*_|\{lambda|<lambda")


def _next_code(text: list, number: int, comment: bool) -> str:
    """The first character of code after line `number` of `text`, past blank
    lines and comments (`comment`: line `number` ends inside one)."""
    for line in text[number:]:
        rest = line.strip()
        while rest:
            if comment:
                end = rest.find("*/")
                rest, comment = (rest[end + 2 :].lstrip(), False) if end >= 0 else ("", True)
            elif rest.startswith("//"):
                rest = ""
            elif rest.startswith("/*"):
                rest, comment = rest[2:], True
            else:
                return rest[0]
    return ""


def _closing_line(text: list, start: int, last: int) -> int:
    """The line where a function that starts on line `start` of `text` (the
    source's lines) closes: the first line at whose end every bracket opened
    since the start of `start` is closed (strings and comments skipped),
    unless its header goes on and its body's brace hasn't opened yet. A
    header goes on after a line that ends in `,`, `:` or `=>`, and before
    one whose code starts with `{`, `:` or `,` (blank and comment lines
    skipped). The body's brace is one outside any bracket: llvm's FN for a
    constructor is its initializer list, whose `v{c}`, `v{{c}}` or `p({c})`
    isn't the body, nor is a JS parameter's `({ a })`. last + 1 if it's
    still open at line `last`; `start` if the source can't be the one the
    coverage is for."""
    depth, quote, comment, opened = 0, None, False, False
    for number in range(start, last + 1):
        if number > len(text):
            return start
        line = text[number - 1]
        i, code = 0, -1
        while i < len(line):
            char = line[i]
            if comment:
                if line.startswith("*/", i):
                    comment = False
                    i += 1
            elif quote:
                if char == "\\":
                    i += 1
                elif char == quote:
                    quote = None
                code = i
            elif line.startswith("//", i):
                break
            elif line.startswith("/*", i):
                comment = True
                i += 1
            else:
                if char in "'\"`":
                    quote = char
                elif char in "([{":
                    if char == "{" and depth <= 0 and not (
                        i and (line[i - 1].isalnum() or line[i - 1] in "_>") and line[i - 2 : i] != "=>"
                    ):
                        opened = True
                    depth += 1
                elif char in ")]}":
                    depth -= 1
                if not char.isspace():
                    code = i
            i += 1
        if quote != "`":
            quote = None
        if depth <= 0:
            following = _next_code(text, number, comment)
            if opened or not (line[: code + 1].endswith((",", ":", "=>")) or following in ("{", ":", ",")):
                return number
    return last + 1


def _function_ranges(cov: FileCov, text=None) -> list:
    """[(start, end_or_None, name, hits)] sorted by start line.

    A function without an end line ends before the next function that
    starts on a later line, unless that is a callback or lambda inside it:
    one that starts before the function's brackets close in `text`, the
    source's lines. Without the source, a callback or lambda counts as a
    function of its own (Istanbul names methods the same way). Functions
    that start on the same line (template instantiations, overloads on one
    line) share that range instead of getting an empty one."""
    fns = [
        (start, end, name, hits)
        for name, (start, end, hits) in cov.functions.items()
        if start is not None
    ]
    # Several functions at one start line (template instantiations, a static
    # inline function per translation unit): a called one last, so it owns the lines.
    fns.sort(key=lambda item: (item[0], item[3] > 0, item[2]))
    if cov.origin == "jacoco":
        return _jacoco_ranges(fns)
    all_starts = sorted({item[0] for item in fns})
    named_starts = sorted({item[0] for item in fns if not SYNTHETIC.search(item[2])})
    synthetic_starts = sorted({item[0] for item in fns if SYNTHETIC.search(item[2])})
    named = set(named_starts)
    result = []
    for start, end, name, hits in fns:
        if end is None:
            index = bisect.bisect_right(all_starts, start)
            end = all_starts[index] - 1 if index < len(all_starts) else None
            if text and end is not None and end + 1 not in named and not SYNTHETIC.search(name):
                index = bisect.bisect_right(named_starts, start)
                limit = named_starts[index] - 1 if index < len(named_starts) else None
                inner = synthetic_starts[
                    bisect.bisect_right(synthetic_starts, start) : (
                        bisect.bisect_right(synthetic_starts, limit) if limit is not None else None
                    )
                ]
                closing = _closing_line(text, start, inner[-1])
                outside = [s for s in inner if s > closing]
                end = outside[0] - 1 if outside else limit
        result.append((start, end, name, hits))
    return result


def _jacoco_ranges(fns: list) -> list:
    """JaCoCo gives a method's first line of code, not its brackets, and
    each lambda as a method of its own, <Class>.lambda$<method>$<n>: a
    method ends before the next function that isn't one of its lambdas."""
    starts = [(start, name) for start, _end, name, _hits in fns]
    result = []
    for start, end, name, hits in fns:
        if end is None:
            cls, _, method = name.split("(", 1)[0].rpartition(".")
            mine = re.compile(r"%s\.lambda\$%s\$\d+$" % (re.escape(cls), re.escape(method)))
            later = [s for s, other in starts if s > start and not mine.match(other)]
            end = later[0] - 1 if later else None
        result.append((start, end, name, hits))
    return result


TRAILING_QUALIFIERS = re.compile(r"(\s*(?:const|volatile|noexcept|&&|&))+$")
JVM_RETURN = re.compile(r"\)\[*(?:[ZBCSIJFDV]|L[^;()]+;)$")


def _without_params(name: str) -> str:
    """The name without its own (last) parameter list: `f(int)::$_0::operator()(int&) const`
    is a lambda in f, not f; JaCoCo's `Header.parse([B)I` is parse."""
    name = name.strip()
    jvm = JVM_RETURN.search(name)
    if jvm:
        name = name[: jvm.start() + 1]
    name = TRAILING_QUALIFIERS.sub("", name)
    depth = 0
    for index in range(len(name) - 1, 0, -1) if name.endswith(")") else ():
        depth += {")": 1, "(": -1}.get(name[index], 0)
        if depth == 0:
            return name[:index].rstrip()
    return name


def _base_names(readable: str) -> tuple:
    """(without parameters, also without a leading return type, also without
    template arguments) of a demangled name, without "(anonymous namespace)::"."""
    first = base = _without_params(readable.replace("(anonymous namespace)::", ""))
    depth, cut = 0, -1
    for index, char in enumerate(base):
        if char in "<(":
            depth += 1
        elif char in ">)":
            depth -= 1
        elif char == " " and depth == 0:
            cut = index
    if cut >= 0:
        base = base[cut + 1 :]
    plain = base
    while plain.endswith(">") and "<" in plain:
        depth = 0
        for index in range(len(plain) - 1, -1, -1):
            if plain[index] == ">":
                depth += 1
            elif plain[index] == "<":
                depth -= 1
                if depth == 0:
                    plain = plain[:index]
                    break
        else:
            break
    return first, base, plain


def _name_forms(readable: str) -> set:
    """Names a user may type for a (demangled) function: the full name, the
    name without parameters, without a leading return type, without
    template arguments, and without its namespace or class."""
    forms = {readable, readable.replace("(anonymous namespace)::", "")}
    first, base, plain = _base_names(readable)
    forms.update((first, base, plain))
    for name in (base, plain):
        forms.add(name.rsplit("::", 1)[-1])
        forms.add(name.rsplit(".", 1)[-1])
    forms.discard("")
    return forms


def _owners(ranges: list, lines) -> dict:
    """{line: (start, end, name, hits) or None}: the innermost named function
    containing each line, else the innermost callback or lambda (a top-level
    one). One sweep over the ranges, which are sorted by start."""
    result = {}
    named: list = []
    synthetic: list = []
    index = 0
    for line in sorted(set(lines)):
        while index < len(ranges) and ranges[index][0] <= line:
            (synthetic if SYNTHETIC.search(ranges[index][2]) else named).append(ranges[index])
            index += 1
        owner = None
        for stack in (named, synthetic):
            while stack and stack[-1][1] is not None and stack[-1][1] < line:
                stack.pop()
            if stack and owner is None:
                owner = stack[-1]
        result[line] = owner
    return result


def _blocked_points(cov: FileCov) -> list:
    """[(line, taken, total)] for branch points where some branch was taken
    and another was evaluated but never taken: the input reaches the guard
    but never satisfies it. Istanbul writes 0 (never "-") for branches that
    never ran, so "another branch was taken" is what shows the point ran.
    JaCoCo-derived data has no branch identities, so points are lines."""
    points: dict = {}
    for (line, block, _branch), hits in cov.branches.items():
        key = (line, None if cov.origin == "jacoco" else block)
        point = points.setdefault(key, [0, 0, 0])  # taken, never taken, total
        point[2] += 1
        if hits:
            point[0] += 1
        elif hits == 0:
            point[1] += 1
    per_line: dict = {}
    for (line, _block), (taken, never, total) in points.items():
        if taken and never:
            agg = per_line.setdefault(line, [0, 0])
            agg[0] += taken
            agg[1] += total
    return sorted((line, agg[0], agg[1]) for line, agg in per_line.items())


# A covered run this short, without branches, between a guard and the code
# it blocks: an early return, or a then-block before an untaken else.
COVERED_SKIP = 8


def _branch_line_sets(cov: FileCov) -> tuple:
    """(lines with branches, lines where some branch was taken, lines whose
    branches were never evaluated: llvm's "-")."""
    per_line: dict = {}
    for (line, _block, _branch), hits in cov.branches.items():
        per_line.setdefault(line, []).append(hits)
    taken = {line for line, hits in per_line.items() if any(hits)}
    unevaluated = {line for line, hits in per_line.items() if all(h is None for h in hits)}
    return set(per_line), taken, unevaluated


def _uncovered_after(cov: FileCov, lines: list, branch_sets: tuple, line: int, end) -> list:
    """Uncovered lines after the guard at `line`, a rough textual estimate of
    what it blocks: the first uncovered run within its function, right
    after the guard or after a short covered run without branches. The run
    ends at the next guard that ran (llvm gives every `switch` case its own
    branches), and the guard's own continuation lines, whose branches never
    ran, don't count; an unentered case label's own line does. `lines` are
    the file's executable lines, sorted."""
    branch_lines, taken, unevaluated = branch_sets
    index = bisect.bisect_right(lines, line)

    def inside(i: int) -> bool:
        return i < len(lines) and (end is None or lines[i] <= end)

    while inside(index) and lines[index] in unevaluated and cov.lines[lines[index]] == 0:
        index += 1  # `|| p[0] != 'R')` after `if (n < 4 ||`
    skipped = 0
    while inside(index) and cov.lines[lines[index]] > 0:
        if skipped == COVERED_SKIP or lines[index] in branch_lines:
            return []
        skipped += 1
        index += 1
    # A case label that never ran is itself blocked code: `case 'B': return 2;`.
    out = [line] if cov.lines.get(line) == 0 else []
    while inside(index) and cov.lines[lines[index]] == 0 and lines[index] not in taken:
        out.append(lines[index])
        index += 1
    return out


# Guards that test NULL, an allocation's result or a size limit: they
# rarely open new code, so they're ranked last.
DEFENSIVE = re.compile(
    r"\b(NULL|nullptr|null|SIZE_MAX|SSIZE_MAX|INT_MAX|UINT_MAX|LONG_MAX)\b|"
    r"\b(malloc|calloc|realloc|strdup|strndup)\s*\("
)
NEGATED_NAME = re.compile(r"^(\}\s*else\s+)?if\s*\(\s*!\s*[A-Za-z_][\w.>-]*\s*\)")


def _condition(guard: str) -> str:
    """The condition of `if (...)`/`while (...)`, without the statement after
    it: `if (c != '{') return NULL;` tests a character, not NULL."""
    match = re.match(r"^\s*(?:\}\s*)?(?:else\s+)?(?:if|while)\s*\(", guard)
    if not match:
        return re.split(r"\b(?:return|goto|throw)\b", guard)[0]
    depth, start = 1, match.end()
    for index in range(start, len(guard)):
        depth += {"(": 1, ")": -1}.get(guard[index], 0)
        if depth == 0:
            return guard[: index + 1]
    return guard  # the condition continues on the next line


def _is_defensive(guard: str, after: list) -> bool:
    # "if (!ptr) goto error;" too, when it blocks only a few lines.
    condition = _condition(guard)
    return bool(DEFENSIVE.search(condition) or (NEGATED_NAME.search(condition) and len(after) <= 5))


# A call: a name and its parenthesis, with explicit template arguments
# (`parse_item<int>(p)`), and the `.`, `->` or `::` before a member's name.
CALL = re.compile(r"(\.|->|::)?\b([A-Za-z_]\w*)\s*(?:<(?=[\w:])[\w\s:,*&<>]{0,100}>\s*)?\(")
# Keywords before a parenthesis aren't calls, unless a member's name (`p.catch(f)`).
KEYWORDS = frozenset(
    "if while for switch catch return sizeof alignof decltype typeof throw new delete case do else "
    "defined function await yield synchronized".split()
)


def _never_called(files: dict) -> tuple:
    """({simple name: display name} of the functions in scope that never ran,
    {file: simple names of its functions that ran}): a static function or
    an overload with the same name may have run in that file."""
    names = demangle([name for cov in files.values() for name in cov.functions])
    never, ran = {}, {}
    for display, cov in files.items():
        for name, (_start, _end, hits) in cov.functions.items():
            shown = names.get(name, name)
            simple = _base_names(shown)[2].rsplit("::", 1)[-1].rsplit(".", 1)[-1].strip()
            if not simple:
                continue
            if hits:
                ran.setdefault(display, set()).add(simple)
            else:
                never.setdefault(simple, shown)
    return never, ran


def _callees(path: str, lines: list, never: dict, ran: set, cache: dict) -> list:
    """Never-called functions that the blocked lines call, but not those
    named like a function in `ran`, the file's functions that ran."""
    found = []
    for line in lines:
        for member, name in CALL.findall(" ".join(_source_lines(path, cache)[line - 1 : line])):
            if (name in KEYWORDS and not member) or name in ran:
                continue
            if name in never and never[name] not in found:
                found.append(never[name])
    return found


def _source_lines(path: str, cache: dict) -> list:
    if not path:
        return []
    if path not in cache:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                cache[path] = handle.read().splitlines()
        except OSError:
            cache[path] = []
    return cache[path]


def _source_line(path: str, line: int, cache: dict) -> str:
    lines = _source_lines(path, cache)
    if not 0 < line <= len(lines):
        return ""
    text = " ".join(lines[line - 1].split())
    return text if len(text) <= 70 else text[:67] + "..."


def _print_blocked(files: dict, originals: dict, source_root, top: int, cache: dict) -> None:
    rows = []
    jacoco = False
    never, ran = _never_called(files)
    for display, cov in files.items():
        points = _blocked_points(cov)
        source = _find_source(display, originals.get(display, ""), source_root)
        owners = _owners(_function_ranges(cov, _source_lines(source, cache)), [p[0] for p in points])
        lines = sorted(cov.lines)
        branch_sets = _branch_line_sets(cov)
        for line, taken, total in points:
            owner = owners[line]
            after = _uncovered_after(cov, lines, branch_sets, line, owner[1] if owner else None)
            guard = _source_line(source, line, cache) if source else ""
            callees = _callees(source, after, never, ran.get(display, set()), cache) if source else []
            rows.append((display, line, owner[2] if owner else "", taken, total, after, guard, callees))
        jacoco = jacoco or cov.origin == "jacoco"
    rows.sort(key=lambda r: (_is_defensive(r[6], r[5]), -len(r[5]), r[0], r[1]))
    names = demangle([r[2] for r in rows if r[2]])
    title = "Blocked lines" if jacoco else "Blocked branches"
    print("## %s (%d)" % (title, len(rows)))
    print()
    if jacoco:
        print("A line where some branches were taken and others never were. JaCoCo")
        print("reports branch counts per line only, so the untaken branch isn't named.")
    else:
        print("A branch never taken at a point where another branch was taken: inputs")
        print("reach the guard but never satisfy it. Diagnose these first.")
    print("Guards that test NULL, an allocation's result or a size limit come last.")
    print()
    if not rows:
        print("None.")
        print()
        return
    print("| File:line | Function | Guard | Taken | Uncovered lines after (estimate) | Never-called callees |")
    print("|---|---|---|---|---|---|")
    for display, line, name, taken, total, after, guard, callees in rows[:top]:
        print(
            "| %s:%d | %s | %s | %d/%d | %s | %s |"
            % (
                display,
                line,
                names.get(name, name).replace("|", "\\|") or "-",
                ("`%s`" % guard.replace("`", "'")).replace("|", "\\|") if guard else "-",
                taken,
                total,
                "%d (%s)" % (len(after), _ranges(after)) if after else "0",
                ", ".join("`%s`" % c.replace("|", "\\|") for c in callees[:3]) or "-",
            )
        )
    if len(rows) > top:
        print()
        print("(%d more not shown; use --top)" % (len(rows) - top))
    print()


# A checkout writes sources and lcov fixtures within moments of each other.
MTIME_SLACK = 2.0


def _stale_note(lcov_paths: list, sources: list) -> str:
    """A note naming the sources ([(display, path)]) changed after the oldest
    lcov file was written: the coverage's line numbers may no longer match."""
    try:
        oldest = min(lcov_paths, key=os.path.getmtime)
        written = os.path.getmtime(oldest) + MTIME_SLACK
        stale = sorted(d for d, path in sources if path and os.path.getmtime(path) > written)
    except (OSError, ValueError):
        return ""
    if not stale:
        return ""
    more = " and %d more" % (len(stale) - 5) if len(stale) > 5 else ""
    return ("**Stale:** %s changed after %s was written: line numbers may have moved. "
            "Replay and export on the current build." % (", ".join(stale[:5]) + more, oldest))


DEFINE = re.compile(r"^\s*#\s*define\b")


def _drop_outside_functions(cov: FileCov, ranges: list, text: list) -> None:
    """llvm repeats a macro's lines and branches at its #define: in C and C++,
    code outside every function, or on a #define that no function starts in
    (a function a macro defines starts there), is a macro definition, not a
    gap of its own. llvm's FN has no end line, so a #define after a function
    is in its range."""
    starts = sorted(r[0] for r in ranges)
    macro = set()
    number = 0
    while number < len(text):
        number += 1
        if DEFINE.match(text[number - 1]):
            first = number
            while text[number - 1].rstrip().endswith("\\") and number < len(text):
                number += 1
            index = bisect.bisect_left(starts, first)
            if index == len(starts) or starts[index] > number:
                macro.update(range(first, number + 1))
    owners = _owners(ranges, list(cov.lines) + [key[0] for key in cov.branches])
    cov.lines = {line: hits for line, hits in cov.lines.items() if owners[line] and line not in macro}
    cov.branches = {key: hits for key, hits in cov.branches.items() if owners[key[0]] and key[0] not in macro}


def cmd_gaps(args) -> int:
    mapper = PathMapper(args.root)
    scope = load_scope(args.scope)
    loaded = _load(args, args.lcov)
    originals = {mapper.display(name): name for name in loaded}
    files, unmatched = filter_scope(loaded, mapper, scope)
    cache: dict = {}
    source_root = args.source_root or args.root
    note = _stale_note(args.lcov, [(d, _find_source(d, originals.get(d, ""), source_root)) for d in files])
    if note:
        print(note)
        print()
    if any(cov.lines for cov in files.values()) and not any(cov.branches for cov in files.values()):
        print("**No branch data:** the export has no branch records, so blocked branches can't be "
              "found; the gaps below are lines only.")
        print()
    for display, cov in files.items():
        if cov.origin != "jacoco" and NATIVE_SOURCE.search(display):
            text = _source_lines(_find_source(display, originals.get(display, ""), source_root), cache)
            _drop_outside_functions(cov, _function_ranges(cov, text), text)
    _print_blocked(files, originals, source_root, args.top, cache)
    entries = []
    for display, cov in files.items():
        text = _source_lines(_find_source(display, originals.get(display, ""), source_root), cache)
        owners = _owners(_function_ranges(cov, text), [key[0] for key in cov.branches] + list(cov.lines))
        groups: dict = {}
        for (line, _block, _branch), hits in cov.branches.items():
            owner = owners[line]
            key = owner[2] if owner else "(outside functions)"
            group = groups.setdefault(
                key, {"untaken": [], "total": 0, "owner": owner, "zero_lines": []}
            )
            group["total"] += 1
            if not hits:
                group["untaken"].append(line)
        for line, hits in cov.lines.items():
            if hits:
                continue
            owner = owners[line]
            key = owner[2] if owner else "(outside functions)"
            group = groups.setdefault(
                key, {"untaken": [], "total": 0, "owner": owner, "zero_lines": []}
            )
            group["zero_lines"].append(line)
        for name, group in groups.items():
            if not group["untaken"] and not group["zero_lines"]:
                continue
            owner = group["owner"]
            called = "n/a" if owner is None else ("yes" if owner[3] > 0 else "no")
            entries.append(
                {
                    "file": display,
                    "name": name,
                    "start": owner[0] if owner else 0,
                    "untaken": group["untaken"],
                    "total": group["total"],
                    "zero_lines": group["zero_lines"],
                    "called": called,
                }
            )
    names = demangle([e["name"] for e in entries])
    entries.sort(
        key=lambda e: (-len(e["untaken"]), -len(e["zero_lines"]), e["file"], e["start"])
    )
    print("## Remaining gaps (%d functions with uncovered code)" % len(entries))
    print()
    if unmatched:
        print("Scope entries with no coverage data: " + ", ".join(unmatched))
        print()
    print("| Function | File:line | Untaken branches | Uncovered lines | Called |")
    print("|---|---|---|---|---|")
    for entry in entries[: args.top]:
        untaken = "%d/%d" % (len(entry["untaken"]), entry["total"])
        if entry["untaken"]:
            untaken += " (lines %s)" % _ranges(entry["untaken"])
        print(
            "| %s | %s:%d | %s | %s | %s |"
            % (
                names.get(entry["name"], entry["name"]).replace("|", "\\|"),
                entry["file"],
                entry["start"],
                untaken,
                _ranges(entry["zero_lines"]) or "-",
                entry["called"],
            )
        )
    if len(entries) > args.top:
        print()
        print("(%d more functions not shown; use --top)" % (len(entries) - args.top))
    return 0


def _find_source(display: str, original: str, source_root) -> str:
    candidates = [original]
    if source_root:
        candidates.append(os.path.join(source_root, display))
    candidates.append(display)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def cmd_show(args) -> int:
    mapper = PathMapper(args.root)
    files = _load(args, args.lcov)
    matches = [
        (name, cov)
        for name, cov in files.items()
        if _suffix_matches(mapper.display(name), args.file)
    ]
    if not matches:
        print("error: %s is not in the coverage data" % args.file, file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(
            "error: %s matches several files: %s"
            % (args.file, ", ".join(mapper.display(n) for n, _ in matches)),
            file=sys.stderr,
        )
        return 1
    name, cov = matches[0]
    display = mapper.display(name)
    source = _find_source(display, name, args.source_root or args.root)
    if not source:
        print("error: cannot find source for %s (use --source-root)" % display, file=sys.stderr)
        return 1
    with open(source, encoding="utf-8", errors="replace") as handle:
        text = handle.read().splitlines()
    first, last = 1, len(text)
    if args.lines:
        match = re.match(r"^(\d+)-(\d+)$", args.lines)
        if not match or not 1 <= int(match.group(1)) <= int(match.group(2)):
            print("error: --lines takes <first>-<last>, e.g. 20-45", file=sys.stderr)
            return 2
        first, last = int(match.group(1)), min(int(match.group(2)), len(text))
    if args.function:
        ranges = _function_ranges(cov, text)
        readable = demangle([r[2] for r in ranges])
        found = [
            r
            for r in ranges
            if r[2] == args.function or args.function in _name_forms(readable.get(r[2], r[2]))
        ]
        if not found:
            hint = ""
            if any(name.startswith("(anonymous_") for name in cov.functions):
                hint = "; Istanbul names methods and callbacks (anonymous_<n>): use --lines <first>-<last>"
            print("error: function %s not found in %s%s" % (args.function, display, hint), file=sys.stderr)
            return 1
        spans = sorted({(r[0], r[1]) for r in found}, key=lambda s: (s[0], s[1] or 0))
        if len(spans) > 1 and any(not SYNTHETIC.search(r[2]) for r in found):
            # a function and a callback or lambda in it: the function
            found = [r for r in found if not SYNTHETIC.search(r[2])]
            spans = sorted({(r[0], r[1]) for r in found}, key=lambda s: (s[0], s[1] or 0))
        if len(spans) > 1:
            print(
                "error: %s matches several functions in %s: %s (use the full name)"
                % (
                    args.function,
                    display,
                    ", ".join("%s (line %d)" % (readable.get(r[2], r[2]), r[0]) for r in found),
                ),
                file=sys.stderr,
            )
            return 1
        first = spans[0][0]
        last = spans[0][1] or len(text)
    branch_lines: dict = {}
    for (line, _block, _branch), hits in cov.branches.items():
        total, untaken = branch_lines.get(line, (0, 0))
        branch_lines[line] = (total + 1, untaken + (0 if hits else 1))
    print("## %s (lines %d-%d)" % (display, first, last))
    note = _stale_note(args.lcov, [(display, source)])
    if note:
        print(note)
    print("Columns: line, hit count ('-' = not executable; '!' marks lines never run).")
    print()
    shown_last = last if args.lines or not args.max_lines else min(last, first + args.max_lines - 1)
    print("```")
    for number in range(first, shown_last + 1):
        code = text[number - 1] if number - 1 < len(text) else ""
        hits = cov.lines.get(number)
        count = "-" if hits is None else str(hits)
        note = ""
        if number in branch_lines:
            total, untaken = branch_lines[number]
            if untaken:
                note = "    <-- %d/%d branches not taken" % (untaken, total)
        marker = "!" if hits == 0 else " "
        print("%6d %8s%s| %s%s" % (number, count, marker, code, note))
    print("```")
    if shown_last < last:
        print("(%d more lines; use --lines <first>-<last>, or --max-lines)" % (last - shown_last))
    return 0


def _covered_keys(files: dict) -> tuple:
    branches = set()
    lines = set()
    for display, cov in files.items():
        for key, hits in cov.branches.items():
            if hits:
                branches.add((display,) + key)
        for line, hits in cov.lines.items():
            if hits:
                lines.add((display, line))
    return branches, lines


def _covered_branches_per_line(files: dict) -> dict:
    """{(display, line): number of branches taken} for JaCoCo-derived data."""
    counts: dict = {}
    for display, cov in files.items():
        for (line, _block, _branch), hits in cov.branches.items():
            if hits:
                counts[(display, line)] = counts.get((display, line), 0) + 1
    return counts


def _overlap_by_identity(a_files: dict, b_files: dict) -> dict:
    a_br, a_ln = _covered_keys(a_files)
    b_br, b_ln = _covered_keys(b_files)
    result = {
        "a": (len(a_br), len(a_ln)),
        "b": (len(b_br), len(b_ln)),
        "both": (len(a_br & b_br), len(a_ln & b_ln)),
        "only": {},
    }
    for label, mine, theirs, mine_ln, theirs_ln in (
        ("A", a_br, b_br, a_ln, b_ln),
        ("B", b_br, a_br, b_ln, a_ln),
    ):
        only_br, only_ln = mine - theirs, mine_ln - theirs_ln
        per_file: dict = {}
        for display, line, _block, _branch in only_br:
            per_file.setdefault(display, [set(), set()])[0].add(line)
        for display, line in only_ln:
            per_file.setdefault(display, [set(), set()])[1].add(line)
        result["only"][label] = (len(only_br), len(only_ln), per_file)
    return result


def _union_mismatch(counts: dict, keys: set, a_ln: set, b_ln: set, u_ln: set) -> str:
    """The first line where the union can't be A and B merged: it covers fewer
    branches than either side, more than both together, misses a line or
    covers one neither covers."""
    for key in sorted(keys | a_ln | b_ln | u_ln):
        x, y, u = (counts[side].get(key, 0) for side in ("a", "b", "u"))
        if not max(x, y) <= u <= x + y or (key in a_ln or key in b_ln) != (key in u_ln):
            return "%s:%d: A %d, B %d, union %d branches" % (key[0], key[1], x, y, u)
    return ""


def _overlap_by_union(a_files: dict, b_files: dict, u_files: dict) -> dict:
    """JaCoCo-derived data has branch counts per line but no branch
    identities, so what only A covers is computed as (A u B) - B per line,
    from a report of the merged .exec files."""
    counts = {
        key: _covered_branches_per_line(files)
        for key, files in (("a", a_files), ("b", b_files), ("u", u_files))
    }
    _, a_ln = _covered_keys(a_files)
    _, b_ln = _covered_keys(b_files)
    _, u_ln = _covered_keys(u_files)
    keys = set(counts["a"]) | set(counts["b"]) | set(counts["u"])
    mismatch = _union_mismatch(counts, keys, a_ln, b_ln, u_ln)
    if mismatch:
        return {"mismatch": mismatch}
    both = sum(
        max(0, counts["a"].get(k, 0) + counts["b"].get(k, 0) - counts["u"].get(k, 0))
        for k in keys
    )
    result = {
        "a": (sum(counts["a"].values()), len(a_ln)),
        "b": (sum(counts["b"].values()), len(b_ln)),
        "both": (both, len(a_ln & b_ln)),
        "only": {},
    }
    for label, other, other_ln in (("A", "b", b_ln), ("B", "a", a_ln)):
        total = 0
        per_file: dict = {}
        for key in keys:
            extra = counts["u"].get(key, 0) - counts[other].get(key, 0)
            if extra > 0:
                total += extra
                per_file.setdefault(key[0], [set(), set()])[0].add(key[1])
        only_ln = u_ln - other_ln
        for display, line in only_ln:
            per_file.setdefault(display, [set(), set()])[1].add(line)
        result["only"][label] = (total, len(only_ln), per_file)
    return result


def _lines_only(files: dict) -> dict:
    return {display: FileCov(cov.path, lines=cov.lines, origin=cov.origin) for display, cov in files.items()}


def _count_differences(a_files: dict, b_files: dict, processes: int = 0) -> tuple:
    """Branches and lines run by either side, sorted into same count, once per
    process (B = processes x A) and other differences: totals [branches, lines]
    per class, and the entries per class and file as (kind, line, a, b)."""
    totals = {"same": [0, 0], "once": [0, 0], "other": [0, 0]}
    entries: dict = {"once": {}, "other": {}}
    for display in sorted(set(a_files) | set(b_files)):
        for kind, attr in ((0, "branches"), (1, "lines")):
            a = getattr(a_files[display], attr) if display in a_files else {}
            b = getattr(b_files[display], attr) if display in b_files else {}
            for key in sorted(set(a) | set(b)):
                x, y = a.get(key) or 0, b.get(key) or 0
                if not x and not y:
                    continue
                cls = "same" if x == y else "once" if processes > 1 and x and y == processes * x else "other"
                totals[cls][kind] += 1
                if cls != "same":
                    entries[cls].setdefault(display, []).append((kind, key[0] if kind == 0 else key, x, y))
    return totals, entries


def _describe_differences(entries: list, limit: int = 12) -> str:
    """`line 4 (A 1, B 2); lines 7-9 (A 1, B 8); branch line 4 (A 1, B 2)`."""
    parts, seen_branch_lines = [], set()
    lines = [(line, x, y) for kind, line, x, y in entries if kind == 1]
    start = None
    for i, (line, x, y) in enumerate(lines):
        if start is None:
            start = (line, x, y)
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if not nxt or nxt[0] != line + 1 or nxt[1:] != (x, y):
            first = start[0]
            label = "line %d" % first if first == line else "lines %d-%d" % (first, line)
            parts.append("%s (A %d, B %d)" % (label, x, y))
            start = None
    for kind, line, x, y in entries:
        if kind == 0 and line not in seen_branch_lines:
            seen_branch_lines.add(line)
            parts.append("branch line %d (A %d, B %d)" % (line, x, y))
    more = len(parts) - limit
    return "; ".join(parts[:limit]) + ("; %d more" % more if more > 0 else "")


def _plural(n: int, word: str, plural: str) -> str:
    return "%d %s" % (n, word if n == 1 else plural)


def _report_counts(args, a_files: dict, b_files: dict, missing: list) -> int:
    processes = args.processes or 0
    totals, entries = _count_differences(a_files, b_files, processes)
    print("## Hit counts: A = %s, B = %s" % (args.a, args.b))
    print()
    print("| | Branches | Lines |")
    print("|---|---|---|")
    print("| Same hit count | %d | %d |" % tuple(totals["same"]))
    if processes > 1:
        print("| Once per process (B = %d × A) | %d | %d |" % ((processes,) + tuple(totals["once"])))
        print("| Other differences | %d | %d |" % tuple(totals["other"]))
    else:
        print("| Different hit counts | %d | %d |" % tuple(totals["other"]))
    print()
    if missing:
        print("**Incomplete:** no coverage data for scope %s: %s." % (
            "entry" if len(missing) == 1 else "entries", ", ".join(missing)))
        print()
    empty = [label for label, files in (("A", a_files), ("B", b_files))
             if not any(v for cov in files.values() for v in list(cov.lines.values()) + list(cov.branches.values()))]
    if empty:
        print("Result: inconclusive: %s %s no coverage. Check the replay and the paths." % (
            " and ".join(empty), "has" if len(empty) == 1 else "have"))
        return 1
    other, once = totals["other"], totals["once"]
    if any(other):
        print("Result: hit counts differ on %s and %s%s." % (
            _plural(other[0], "branch", "branches"), _plural(other[1], "line", "lines"),
            " beyond once-per-process code" if processes > 1 else ""))
    elif any(once):
        print("Result: hit counts differ only on code that runs once per process: setup (module loading, "
              "initialization) or work skipped after the first input. Check which.")
    else:
        print("Result: A and B have the same hit count on every branch and line.")
    for cls, title in (("once", "Once per process"), ("other", "Different hit counts")):
        if entries[cls]:
            print()
            print("### %s" % title)
            for display in sorted(entries[cls])[: args.top]:
                print("- %s: %s" % (display, _describe_differences(entries[cls][display])))
    return 0


def cmd_overlap(args) -> int:
    mapper = PathMapper(args.root)
    scope = load_scope(args.scope)
    a_files, a_missing = filter_scope(_load(args, [args.a]), mapper, scope)
    b_files, b_missing = filter_scope(_load(args, [args.b]), mapper, scope)
    if args.counts:
        if is_jacoco(a_files) or is_jacoco(b_files):
            raise UsageError(
                "JaCoCo-derived lcov records whether code ran, not how often: compare the covered sets "
                "(overlap without --counts)."
            )
        return _report_counts(args, a_files, b_files, sorted(set(a_missing) | set(b_missing)))
    if is_jacoco(a_files) or is_jacoco(b_files):
        if not args.union:
            raise UsageError(
                "JaCoCo-derived lcov has no branch identities, so overlap can't compare "
                "A and B directly. Merge their .exec files with `jacococli merge`, "
                "convert the merged report, and pass it as --union."
            )
        u_files, _ = filter_scope(_load(args, [args.union]), mapper, scope)
        if not is_jacoco(u_files):
            raise UsageError("--union must be the JaCoCo-derived lcov of A and B merged")
        result = _overlap_by_union(a_files, b_files, u_files)
        if "mismatch" in result:
            print("Result: inconclusive: --union isn't A and B merged (%s). Merge their .exec files "
                  "again and convert the report." % result["mismatch"])
            return 1
        no_branches = []
    else:
        # A side without branch records (a line-only export) would count as
        # covering no branches: compare lines only.
        no_branches = [label for label, files in (("A", a_files), ("B", b_files))
                       if not any(cov.branches for cov in files.values())]
        if no_branches:
            a_files, b_files = _lines_only(a_files), _lines_only(b_files)
        result = _overlap_by_identity(a_files, b_files)
    only_a, only_b = result["only"]["A"], result["only"]["B"]
    print("## Overlap: A = %s, B = %s" % (args.a, args.b))
    print()
    if no_branches:
        print("**No branch data:** %s %s no branch records; lines only." % (
            " and ".join(no_branches), "has" if len(no_branches) == 1 else "have"))
        print()
    row = "| %s | n/a | %d |" if no_branches else "| %s | %d | %d |"
    print("| | Branches | Lines |")
    print("|---|---|---|")
    for title, numbers in (("Covered by A", result["a"]), ("Covered by B", result["b"]),
                           ("Covered by both", result["both"]), ("Only A", only_a[:2]), ("Only B", only_b[:2])):
        print(row % ((title,) + (tuple(numbers[1:]) if no_branches else tuple(numbers))))
    print()
    missing = sorted(set(a_missing) | set(b_missing))
    if missing:
        print("**Incomplete:** no coverage data for scope %s: %s." % (
            "entry" if len(missing) == 1 else "entries", ", ".join(missing)))
        print()
    a_adds = only_a[0] or only_a[1]
    b_adds = only_b[0] or only_b[1]
    empty = [label for label, key in (("A", "a"), ("B", "b")) if not any(result[key])]
    if empty:
        # Two empty reports would otherwise "cover exactly the same".
        print("Result: inconclusive: %s %s no coverage%s. Check the replay and the paths." % (
            " and ".join(empty), "has" if len(empty) == 1 else "have", " in scope" if scope else ""))
        return 1
    if not a_adds and not b_adds:
        print("Result: A and B cover %s." % ("the same lines" if no_branches else "exactly the same branches and lines"))
    elif not a_adds:
        print("Result: A covers nothing that B doesn't cover.")
    elif not b_adds:
        print("Result: B covers nothing that A doesn't cover.")
    else:
        print("Result: each covers code the other doesn't.")
    for label, (_n_br, _n_ln, per_file) in (("Only A", only_a), ("Only B", only_b)):
        if not per_file:
            continue
        print()
        print("### %s" % label)
        for display in sorted(per_file)[: args.top]:
            br_lines, ln_lines = per_file[display]
            print(
                "- %s: branch lines %s; lines %s"
                % (display, _ranges(list(br_lines)) or "-", _ranges(list(ln_lines)) or "-")
            )
    return 0


TO_CONFIRM = "reached (line; a branch on it is untaken: confirm)"


def cmd_sites(args) -> int:
    mapper = PathMapper(args.root)
    files, _ = filter_scope(_load(args, args.lcov), mapper, [])
    with open(args.sites, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(
            (row for row in handle if not row.lstrip().startswith("#")), delimiter="\t"
        )
        fields = reader.fieldnames or []
        if "file" not in fields or "line" not in fields:
            print("error: sites file needs a header with 'file' and 'line' columns", file=sys.stderr)
            return 1
        sites = list(reader)
    results = []
    groups: dict = {}
    for index, site in enumerate(sites, 1):
        path = (site.get("file") or "").strip()
        try:
            line = int((site.get("line") or "").strip())
        except ValueError:
            continue
        matches = [d for d in files if _entry_matches(d, path)]
        hits = None
        untaken = False
        for display in matches:
            value = files[display].lines.get(line)
            if value is not None:
                hits = (hits or 0) + value
            untaken = untaken or any(
                not h for (branch_line, _block, _branch), h in files[display].branches.items() if branch_line == line
            )
        if matches and hits is not None:
            # Coverage is per line: in `if (x == 42) sink();` the line runs without the call.
            state = "not reached" if not hits else (TO_CONFIRM if untaken else "reached")
        elif matches:
            state = "unknown (no line data)"
        else:
            state = "unknown (file not in coverage)"
        site_id = (site.get("id") or str(index)).strip()
        group = (site.get("group") or "").strip()
        results.append((site_id, group, path, line, state, hits))
        if group:
            entry = groups.setdefault(group, [0, 0])
            entry[1] += 1
            if state.startswith("reached"):
                entry[0] += 1
    reached = sum(1 for r in results if r[4].startswith("reached"))
    confirm = sum(1 for r in results if r[4] == TO_CONFIRM)
    print("## Sink sites: %d of %d reached%s"
          % (reached, len(results), " (%d to confirm)" % confirm if confirm else ""))
    if groups:
        print()
        print("| Group | Sites reached |")
        print("|---|---|")
        for group in sorted(groups):
            hit, total = groups[group]
            print("| %s | %d/%d |" % (group, hit, total))
    print()
    print("| Id | Group | Location | State | Hits |")
    print("|---|---|---|---|---|")
    for site_id, group, path, line, state, hits in results:
        print(
            "| %s | %s | %s:%d | %s | %s |"
            % (site_id, group or "-", path, line, state, "-" if hits is None else hits)
        )
    return 0


def jacoco_to_lcov(xml_path: str, source_roots: list) -> dict:
    tree = ET.parse(xml_path)
    files: dict = {}
    for package in tree.getroot().iter("package"):
        pkg = package.get("name", "")
        methods_by_file: dict = {}
        for cls in package.findall("class"):
            sourcefile = cls.get("sourcefilename")
            if not sourcefile:
                continue
            simple = cls.get("name", "").rsplit("/", 1)[-1]
            for method in cls.findall("method"):
                line = method.get("line")
                if line is None:
                    continue
                covered = 0
                for counter in method.findall("counter"):
                    if counter.get("type") == "METHOD":
                        covered = int(counter.get("covered", "0"))
                methods_by_file.setdefault(sourcefile, []).append(
                    (
                        "%s.%s" % (simple, method.get("name", "?")),
                        method.get("desc", ""),
                        int(line),
                        covered,
                    )
                )
        for source in package.findall("sourcefile"):
            name = source.get("name", "")
            rel = "%s/%s" % (pkg, name) if pkg else name
            path = rel
            for root in source_roots:
                candidate = os.path.join(root, rel)
                if os.path.isfile(candidate):
                    path = _norm(candidate)
                    break
            else:
                if source_roots:
                    path = _norm(os.path.join(source_roots[0], rel))
            cov = files.setdefault(path, FileCov(path))
            cov.origin = "jacoco"
            for line in source.findall("line"):
                number = int(line.get("nr", "0"))
                mi, ci = int(line.get("mi", "0")), int(line.get("ci", "0"))
                mb, cb = int(line.get("mb", "0")), int(line.get("cb", "0"))
                if mi + ci > 0:
                    cov.add_line(number, 1 if ci > 0 else 0)
                for index in range(mb + cb):
                    cov.add_branch((number, "0", str(index)), 1 if index < cb else 0)
            methods = methods_by_file.get(name, [])
            counts: dict = {}
            for label, _desc, _line, _cov in methods:
                counts[label] = counts.get(label, 0) + 1
            for label, desc, line, covered in methods:
                fname = label if counts[label] == 1 else "%s%s" % (label, desc)
                cov.add_function(fname, line, None, 1 if covered else 0)
    return files


def cmd_from_jacoco(args) -> int:
    files = jacoco_to_lcov(args.xml, args.source_root or [])
    if args.output:
        with open(args.output, "w", encoding="utf-8") as out:
            write_lcov(files, out)
    else:
        write_lcov(files, sys.stdout)
    return 0


# --------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coverage.py",
        description="Summarize lcov coverage for fuzzing campaigns.",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    def with_root(p):
        p.add_argument("--root", help="repository root; paths are shown relative to it")

    def with_sf_base(p):
        p.add_argument(
            "--sf-base",
            metavar="DIR",
            help="resolve relative SF: paths against DIR (Istanbul: the replay's working directory)",
        )

    def with_scope(p):
        p.add_argument(
            "--scope",
            metavar="LIST",
            action="append",
            help="text file with one scope path per line (file, or dir/); '#' starts a comment; repeatable",
        )

    p = sub.add_parser("from-jacoco", help="convert JaCoCo XML to lcov")
    p.add_argument("xml")
    p.add_argument(
        "--source-root",
        action="append",
        help="source root such as src/main/java (repeatable); used to build file paths",
    )
    p.add_argument("-o", "--output", help="output file (default: stdout)")
    p.set_defaults(func=cmd_from_jacoco)

    p = sub.add_parser("merge", help="merge lcov files")
    p.add_argument("lcov", nargs="+")
    p.add_argument("-o", "--output", help="output file (default: stdout)")
    p.add_argument("--root", help="repository root; written paths are made relative to it")
    with_sf_base(p)
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("summary", help="coverage totals and per-file table")
    p.add_argument("lcov", nargs="+", help="lcov file(s); several are merged")
    with_root(p)
    with_sf_base(p)
    with_scope(p)
    p.add_argument("--prev", help="previous lcov snapshot to compare with")
    p.add_argument("--top", type=int, default=40, help="max files listed (default 40)")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("gaps", help="blocked branches, then functions with untaken branches, ranked")
    p.add_argument("lcov", nargs="+", help="lcov file(s); several are merged")
    with_root(p)
    with_sf_base(p)
    with_scope(p)
    p.add_argument("--top", type=int, default=25, help="max rows listed per table (default 25)")
    p.add_argument(
        "--source-root",
        help="where to find sources if lcov paths differ (they show the guards, and which callbacks are inside a function)",
    )
    p.set_defaults(func=cmd_gaps)

    p = sub.add_parser("show", help="annotated source of one file")
    p.add_argument("lcov", nargs="+", help="lcov file(s); several are merged")
    p.add_argument("--file", required=True, help="source file (path or unique suffix)")
    only = p.add_mutually_exclusive_group()
    only.add_argument("--function", help="limit output to one function")
    only.add_argument("--lines", help="limit output to lines <first>-<last>, e.g. for a function the data doesn't name")
    p.add_argument("--max-lines", type=int, default=400, help="max lines printed without --lines (default 400; 0: all)")
    with_root(p)
    with_sf_base(p)
    p.add_argument("--source-root", help="where to find sources if lcov paths differ")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("overlap", help="what only A or only B covers")
    p.add_argument("a")
    p.add_argument("b")
    with_root(p)
    with_sf_base(p)
    with_scope(p)
    p.add_argument(
        "--union",
        metavar="LCOV",
        help="JaCoCo only: lcov of A and B merged at the .exec level (required for JaCoCo-derived input)",
    )
    p.add_argument("--top", type=int, default=20, help="max files listed per side")
    p.add_argument(
        "--counts",
        action="store_true",
        help="compare hit counts per branch and line, not covered sets (the gate's state-leak check; not JaCoCo)",
    )
    p.add_argument(
        "--processes",
        type=int,
        metavar="N",
        help="with --counts: B ran in N processes; code with B = N x A runs once per process and is listed apart",
    )
    p.set_defaults(func=cmd_overlap)

    p = sub.add_parser("sites", help="reached / not reached per sink site")
    p.add_argument("lcov", nargs="+", help="lcov file(s); several are merged")
    with_root(p)
    with_sf_base(p)
    p.add_argument(
        "--sites",
        required=True,
        help="TSV with a header; needs 'file' and 'line', optional 'id' and 'group'",
    )
    p.set_defaults(func=cmd_sites)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    except ET.ParseError as error:
        print("error: invalid XML: %s" % error, file=sys.stderr)
        return 1
    except UsageError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # `| head` ends it quietly
    sys.exit(main())
