#!/usr/bin/env python3
"""Check the skills in this repository against the writing rules.

Errors (exit code 1):
  - missing or malformed frontmatter; keys other than name, description, license
  - name not matching the directory or the naming pattern/prefix
  - description missing or over its limit; all descriptions over the budget
  - license other than MIT
  - relative links that leave the skill directory or point nowhere
  - plugin.json "skills" not matching the skill directories; a
    marketplace.json entry whose keywords, description or version differ
    from plugin.json's
  - a Copilot CLI or Claude Code start command in README.md or
    fuzz-campaign's autonomous.md without the deny rules for git push,
    gh issue, gh pr and gh gist
  - a rule stated once (SINGLE_SOURCES, e.g. the hold-back rule) restated
    in another skill file
  - SKILL.md sections out of order (SECTIONS)
  - a workflow skill for single-step requests whose Next doesn't say that a
    standalone request stops
  - a shared skill that uses the fuzzing workspace (.seclab/fuzz)
  - a line several skills state alike (SHARED_LINES: the Jazzer status, the
    workflow skills' "Run every step", the standalone skills' "Before
    stopping") differs
  - a term in another meaning (TERMS: "leak check" for the state-leak
    check, an action's "target" for its aim)
  - a rule stated without its exception (QUALIFIED_PHRASES: constant
    arguments, unless the object called on comes from input)
  - a reference file its SKILL.md doesn't link
  - a pointer into another skill (`fuzz-harness`'s build.md), a script path
    next to a skill (init.py) or a .seclab/fuzz/bin/ script that doesn't exist
  - a symptom table (| Symptom | ...) outside a "## Common failures" section
    that ends its file
  - a language skill without one of LANGUAGE_REFERENCES or a Tested versions
    section
  - a pointer into the language skill ("the language skill's run.md, \"Title\"")
    whose file or title one language skill lacks, or one that names no file
  - a pointer into another file that names a step by number ("gate.md,
    check 3"), or by a title ("gate.md, \"Reaches the target\"") that isn't
    a heading or bold step title in that file
  - brace expansion ({1..3}) in an sh block: sh doesn't expand it
  - a launcher command that doesn't use env.sh's $L (`L=.seclab/...`,
    `python3 $L`)
  - a README skill-table row whose example request isn't in its skill's
    description (agents pick a skill by its description)
  - no skills found at all

Warnings (reported, exit code 0):
  - SKILL.md over 500 lines; language skill files over 150 lines, unless
    LONG_FILES gives the reason (and then over its cap)
  - reference files linking to other reference files (keep one level deep)

Usage: python3 tools/check_skills.py [repo-root]
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED_KEYS = {"name", "description", "license"}
PREFIXES = {"fuzzing": "fuzz-", "shared": "security-"}
ENTRY_SKILLS = {
    "fuzz-campaign",
    "fuzz-harness",
    "fuzz-coverage",
    "fuzz-triage",
    "security-threat-model",
}
LANGUAGE_SKILLS = {"fuzz-c-cpp", "fuzz-java", "fuzz-js"}
# Every language skill has these references and a "Tested versions" section,
# so the workflow skills can point to "the language skill's run.md" whatever the language.
LANGUAGE_REFERENCES = ("install.md", "harness.md", "build.md", "run.md", "coverage.md", "triage.md", "regression.md")
# Workflow skills that also serve single-step requests: those stop when done.
STANDALONE_SKILLS = {"fuzz-harness", "fuzz-coverage", "fuzz-triage"}
# The SKILL.md section order; a skill may leave one out or add its own between them.
SECTIONS = ("When to use", "Start", "Reads", "Steps", "Done when", "Pitfalls and shortcuts to reject", "Writes", "Next")
ENTRY_LIMIT = 350
OTHER_LIMIT = 200
TOTAL_LIMIT = 4000
SKILL_LINES = 500
LANGUAGE_LINES = 150
# Language-skill files past LANGUAGE_LINES, with the reason: path -> (lines allowed, why).
# A new overrun, or a listed file growing past its cap, still warns.
LONG_FILES = {
    "skills/fuzzing/fuzz-c-cpp/references/build.md": (180, "variants, build-system recipes, in-tree builds, instrumentation checks"),
    "skills/fuzzing/fuzz-c-cpp/references/aflpp.md": (215, "AFL++'s builds, checks and long runs, read only for AFL++ long runs"),
    "skills/fuzzing/fuzz-c-cpp/references/run.md": (165, "libFuzzer and AFL++ commands for each kind of run"),
    "skills/fuzzing/fuzz-c-cpp/references/fuzztest.md": (260, "FuzzTest's whole path, coverage included, read only for FuzzTest projects"),
    "skills/fuzzing/fuzz-java/references/build.md": (175, "Maven, and Gradle with one module or several"),
    "skills/fuzzing/fuzz-java/references/harness.md": (194, "parameter styles, seeds, the bug-detector table and the hook API"),
    "skills/fuzzing/fuzz-java/references/run.md": (200, "standalone runs and JUnit runs"),
    "skills/fuzzing/fuzz-java/references/triage.md": (160, "reproduction, minimization, the typed-input decoder"),
    "skills/fuzzing/fuzz-java/references/coverage.md": (190, "JaCoCo setup, replay, export and combining"),
    "skills/fuzzing/fuzz-js/references/harness.md": (197, "ES modules, HTTP-handler and object targets, the bug-detector table and the hook API"),
    "skills/fuzzing/fuzz-js/references/jest.md": (340, "the whole Jest path, read only when the Jest probe passes"),
}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
START_COMMAND_RE = re.compile(r"`(copilot|claude) (--[^`]*)`")
PUBLISHING = ("git push", "gh issue", "gh pr", "gh gist")
DENY_RULES = {
    "copilot": {c: "--deny-tool 'shell(%s)'" % c for c in PUBLISHING},
    "claude": {c: "'Bash(%s *)'" % c for c in PUBLISHING},
}
# Brace expansion in an sh block: bash and zsh only; sh (dash) passes {1..3} on as it is.
BRACE_RANGE_RE = re.compile(r"\{[^{}\s]+\.\.[^{}\s]+\}")
# Pointers the link check doesn't see: `fuzz-harness`'s build.md, init.py by its path next to the skill,
# and the scripts init.py copies to .seclab/fuzz/bin/.
POINTER_RE = re.compile(r"`((?:fuzz|security)-[a-z0-9-]+)`'s ([A-Za-z0-9_.-]+\.md)")
# Pointers into another file name the step or section by its title, since numbers shift when steps move:
# (`fuzz-triage`, "Reproduce"), `fuzz-harness`'s gate.md, "Reaches the target", diagnosis.md, "Choosing the
# next action".
# Pointers inside a file keep numbers.
_SKILL = r"`(?:fuzz|security)-[a-z0-9-]+`"
_FILE = r"(?:\[(?:references/)?[A-Za-z0-9_-]+\.md\]\([^)\s]*\)|(?<![\w/.-])`?[A-Za-z0-9_-]+\.md`?)"
_TITLES = r"(\"[^\"]+\"(?:(?:,| and| or|, and) \"[^\"]+\")*)"
TITLE_POINTER_RE = re.compile(
    r"`((?:fuzz|security)-[a-z0-9-]+)`(?:'s ([A-Za-z0-9_-]+\.md))?(?:,|'s) " + _TITLES
    + r"|(?:\[(?:references/)?([A-Za-z0-9_-]+\.md)\]\([^)\s]*\)|(?<![\w/.-])`?([A-Za-z0-9_-]+\.md)`?)(?:,|'s) " + _TITLES)
NUMBERED_POINTER_RE = re.compile(
    r"(?:%s(?:'s [A-Za-z0-9_-]+\.md)?|%s)(?:'s|,)? (?:[A-Z][a-z]+ )?(?:[Ss]teps?|[Cc]hecks?) \d" % (_SKILL, _FILE)
    + r"|\b(?:[Ss]teps?|[Cc]hecks?) \d+(?:(?:, | and | or |[–-])\d+)* (?:of %s|\((?:%s, )?%s\))" % (_SKILL, _SKILL, _FILE))
# What a title pointer can name: a heading (without its number), or the bold title that starts a list
# item or a paragraph; also without a trailing parenthetical, or up to its first comma ("Rank all
# candidates cheaply, in this order:" is "Rank all candidates cheaply").
# A pointer into whichever language skill the project uses: "the language skill's run.md", with an optional
# title ("the language skill's run.md, \"Merging a run's corpus back\""). Every language skill must have
# the file and the title; a pointer that names no file ("the language skill's merge command") can't be checked.
GENERIC_POINTER_RE = re.compile(r"(?i:the) language skill's `?([A-Za-z0-9_-]+\.md)`?(?:, " + _TITLES + ")?")
UNCHECKED_GENERIC_RE = re.compile(r"(?i:the) language skill's (?!`?[A-Za-z0-9_-]+\.md)\S+(?: \S+)?|\blanguage skill, `?[A-Za-z0-9_-]+\.md")
ANCHOR_RE = re.compile(r"\s*(?:#{1,6}\s+(?:\d+\.\s+)?(.+?)\s*#*$|(?:\d+\.|[-*])\s+\*\*(.+?)\*\*|\*\*(.+?)\*\*)")
SCRIPT_PATH_RE = re.compile(r"<(this skill's|[a-z0-9-]+ skill) directory>/([A-Za-z0-9_./-]+\.py)")
BIN_SCRIPT_RE = re.compile(r"(?:\.seclab/fuzz|\$F)/bin/([A-Za-z0-9_]+\.py)")
# env.sh's L is a command (python3 $F/bin/launcher.py): a relative L breaks after a cd.
OWN_LAUNCHER_RE = re.compile(r"\bL=\.?\.?/?\.seclab|python3 \$L\b")
START_COMMAND_FILES = ("README.md", os.path.join("skills", "fuzzing", "fuzz-campaign", "references", "autonomous.md"))
# A README skill-table row; its last cell's quoted phrases are example requests: | `skill` | use | "request" |
ROUTING_ROW_RE = re.compile(r"^\| `([a-z0-9-]+)` \|.*\|([^|]*)\|\s*$")
# Rules stated once and referenced elsewhere: a phrase of the rule -> (its only file, the pointer to use).
SINGLE_SOURCES = {
    "use-after-free read or write": (
        os.path.join("skills", "fuzzing", "fuzz-campaign", "SKILL.md"),
        "the hold-back rule (`fuzz-campaign`, safety rules)",
    ),
    "filtered by default": (
        os.path.join("skills", "fuzzing", "fuzz-triage", "SKILL.md"),
        "the blocking-bug rule (`fuzz-triage`, blocking bugs)",
    ),
    "cost only breaks ties": (
        os.path.join("skills", "fuzzing", "fuzz-coverage", "references", "diagnosis.md"),
        "the next-action rule (`fuzz-coverage`'s diagnosis.md)",
    ),
    "unless the object they're called on": (
        os.path.join("skills", "shared", "security-threat-model", "SKILL.md"),
        "the sink drop rule (`security-threat-model`, \"Find sinks\")",
    ),
    "only from the fuzzing engine": (
        os.path.join("skills", "fuzzing", "fuzz-triage", "SKILL.md"),
        "the engine-leak rule (`fuzz-triage`, \"Group\")",
    ),
    "fixed-up input": (
        os.path.join("skills", "fuzzing", "fuzz-triage", "SKILL.md"),
        "the fuzzing-only-code check (`fuzz-triage`, \"Find the root cause\")",
    ),
    "LSAN_OPTIONS=suppressions": (
        os.path.join("skills", "fuzzing", "fuzz-c-cpp", "references", "run.md"),
        "the suppressions command (`fuzz-c-cpp`'s run.md, \"Environment (`--env`)\")",
    ),
    "doesn't build after one attempt": (
        os.path.join("skills", "fuzzing", "fuzz-campaign", "references", "targets.md"),
        "the baseline's failed-build rule (`fuzz-campaign`'s targets.md, \"Baseline\")",
    ),
    "lcov names the compiled": (
        os.path.join("skills", "fuzzing", "fuzz-js", "references", "coverage.md"),
        "the TypeScript paths rule (`fuzz-js`'s coverage.md, \"TypeScript\")",
    ),
    "another JDK than": (
        os.path.join("skills", "fuzzing", "fuzz-java", "references", "build.md"),
        "the build tools' JDK (`fuzz-java`'s build.md, \"JDK\")",
    ),
}

# Rules whose exception must follow them: a phrase of the rule -> the start of its exception (within 120 characters).
QUALIFIED_PHRASES = {
    "arguments are constants": "unless the object",
    "faster than the size": "small input",
    "only if its aim becomes covered": "scope coverage",
    "go upstream as ordinary bugs": "unless a condition above holds",
}

# One meaning per term: a phrase of another use (case-insensitive, outside HTML comments) -> what to write instead.
TERMS = {
    r"(?<!state-)\bleak[- ]check": "state-leak check (gate check 4; LeakSanitizer finds leaks)",
    r"\bits target\b|\btarget (?:becomes|became) covered": "aim (what an action should cover; a target is an entry point)",
}

# The ledger's Open crashes columns whose values other skills set: each value set must be in the legend.
LEDGER = os.path.join("skills", "fuzzing", "fuzz-campaign", "references", "ledger.md")
LEDGER_COLUMNS = ("Progress", "Report")
LEDGER_VALUE_RE = re.compile(r"\b(%s) (?:to )?`([^`]+)`" % "|".join(LEDGER_COLUMNS))

# Lines that several skills state alike: (prefix, files that must have the same line).
_WORKFLOW_SKILLS = ("fuzz-campaign", "fuzz-harness", "fuzz-coverage", "fuzz-triage")
SHARED_LINES = (
    ("**Status:**", (os.path.join("skills", "fuzzing", "fuzz-java", "SKILL.md"),
                     os.path.join("skills", "fuzzing", "fuzz-js", "SKILL.md"))),
    ("**Run every step**", tuple(os.path.join("skills", "fuzzing", name, "SKILL.md") for name in _WORKFLOW_SKILLS)),
    ("- **Before stopping,**", tuple(os.path.join("skills", "fuzzing", name, "SKILL.md") for name in sorted(STANDALONE_SKILLS))),
)


def strip_code(text: str) -> str:
    """Remove fenced code blocks and inline code spans, whose brackets and
    parentheses aren't Markdown links."""
    kept = []
    fence = None
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if match:
            if fence is None:
                fence = match.group(1)
            elif match.group(1) == fence:
                fence = None
            continue
        if fence is None:
            kept.append(INLINE_CODE_RE.sub("", line))
    return "\n".join(kept)


class Report:
    def __init__(self) -> None:
        self.errors: list = []
        self.warnings: list = []

    def error(self, where: str, message: str) -> None:
        self.errors.append("%s: %s" % (where, message))

    def warn(self, where: str, message: str) -> None:
        self.warnings.append("%s: %s" % (where, message))


def parse_frontmatter(text: str):
    """Parse the simple YAML subset used in SKILL.md frontmatter.

    Supports `key: value`, quoted values, and folded/literal block scalars
    (`>`, `>-`, `|`, `|-`). Returns (dict, body) or (None, text).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return None, text
    data: dict = {}
    index = 1
    while index < end:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            raise ValueError("cannot parse frontmatter line %d: %r" % (index + 1, line))
        key, value = match.group(1), match.group(2).strip()
        index += 1
        if value in (">", ">-", "|", "|-", ""):
            # Block scalar, or a nested mapping such as `metadata:`.
            block = []
            while index < end and (lines[index].startswith((" ", "\t")) or not lines[index].strip()):
                block.append(lines[index].strip())
                index += 1
            if value.startswith("|"):
                value = "\n".join(block).strip()
            elif value.startswith(">"):
                value = " ".join(part for part in block if part)
            else:
                value = "\n".join(part for part in block if part)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
            if match.group(2).strip()[0] == '"':
                value = value.replace('\\"', '"').replace("\\\\", "\\")
            else:
                value = value.replace("''", "'")
        data[key] = value
    body = "\n".join(lines[end + 1 :])
    return data, body


def find_skills(root: str) -> list:
    skills = []
    base = os.path.join(root, "skills")
    if not os.path.isdir(base):
        return skills
    for dirpath, _dirnames, filenames in os.walk(base):
        if "SKILL.md" in filenames:
            skills.append(dirpath)
    return sorted(skills)


def check_links(report: Report, skill_dir: str, root: str) -> None:
    skill_real = os.path.realpath(skill_dir)
    for dirpath, _dirnames, filenames in os.walk(skill_dir):
        for filename in filenames:
            if not filename.endswith(".md"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            in_references = os.path.basename(dirpath) == "references"
            for target in LINK_RE.findall(strip_code(text)):
                if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                    continue
                target_path = target.split("#", 1)[0]
                if not target_path:
                    continue
                resolved = os.path.realpath(os.path.join(dirpath, target_path))
                if not (resolved == skill_real or resolved.startswith(skill_real + os.sep)):
                    report.error(rel, "link leaves the skill directory: %s (refer to other skills by name)" % target)
                    continue
                if not os.path.exists(resolved):
                    report.error(rel, "broken link: %s" % target)
                    continue
                if (
                    in_references
                    and resolved.endswith(".md")
                    and os.path.basename(os.path.dirname(resolved)) == "references"
                ):
                    report.warn(rel, "reference links to another reference (%s); keep references one level deep" % target)


def check_orphans(report: Report, skill_dir: str, root: str, skill_text: str) -> None:
    """An agent finds a reference through its SKILL.md: every one is linked from there."""
    for path in sorted(glob.glob(os.path.join(skill_dir, "references", "*.md"))):
        target = "references/" + os.path.basename(path)
        if "(%s)" % target not in skill_text:
            report.error(os.path.relpath(path, root), "not linked from its SKILL.md ([%s](%s))" % (target, target))


def count_lines(path: str) -> int:
    with open(path, encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def check_section_order(report: Report, rel: str, text: str) -> None:
    last = None
    for heading in re.findall(r"^## (.+?)\s*$", strip_code(text), re.M):
        index = next((i for i, name in enumerate(SECTIONS) if heading == name or heading.startswith(name + " ")), None)
        if index is None:
            continue
        if last is not None and index < last[0]:
            report.error(rel, "section %r comes after %r; the order is: %s"
                         % (heading, last[1], ", ".join(SECTIONS)))
        last = (index, heading)


def check_size(report: Report, rel: str, root: str) -> None:
    """A language-skill file over LANGUAGE_LINES warns, unless LONG_FILES gives its reason and cap."""
    n = count_lines(os.path.join(root, rel))
    key = rel.replace(os.sep, "/")
    cap = LONG_FILES.get(key, (LANGUAGE_LINES, ""))[0]
    if n > cap:
        allowed = " (LONG_FILES allows %d)" % cap if key in LONG_FILES else ""
        report.warn(rel, "%d lines; language skill files aim for about %d%s" % (n, LANGUAGE_LINES, allowed))
    elif key in LONG_FILES and n <= LANGUAGE_LINES:
        report.warn(rel, "%d lines: drop its LONG_FILES entry" % n)


def check(root: str) -> Report:
    report = Report()
    skills = find_skills(root)
    if not skills:
        report.error("skills/", "no skills found")
        return report
    total = 0
    names = []
    descriptions = {}
    for skill_dir in skills:
        rel = os.path.relpath(skill_dir, root)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md, encoding="utf-8") as handle:
            text = handle.read()
        try:
            meta, _body = parse_frontmatter(text)
        except ValueError as error:
            report.error(rel, str(error))
            continue
        if meta is None:
            report.error(rel, "SKILL.md has no frontmatter")
            continue
        dirname = os.path.basename(skill_dir)
        name = meta.get("name", "")
        names.append(dirname)
        unknown = sorted(set(meta) - ALLOWED_KEYS)
        if unknown:
            report.error(rel, "unknown frontmatter keys (not portable): %s" % ", ".join(unknown))
        if not name:
            report.error(rel, "missing name")
        elif name != dirname:
            report.error(rel, "name %r does not match directory %r" % (name, dirname))
        elif not NAME_RE.match(name) or len(name) > 64:
            report.error(rel, "name %r must be lowercase words joined by hyphens, max 64 chars" % name)
        bucket = os.path.basename(os.path.dirname(skill_dir))
        prefix = PREFIXES.get(bucket)
        if prefix and name and not name.startswith(prefix):
            report.error(rel, "skills in %s/ must start with %r" % (bucket, prefix))
        description = meta.get("description", "")
        descriptions[dirname] = description
        limit = ENTRY_LIMIT if name in ENTRY_SKILLS else OTHER_LIMIT
        if not description:
            report.error(rel, "missing description")
        elif len(description) > limit:
            report.error(rel, "description has %d characters; limit is %d" % (len(description), limit))
        total += len(description)
        if meta.get("license") != "MIT":
            report.error(rel, "license must be MIT")
        check_section_order(report, rel, text)
        if name in STANDALONE_SKILLS and "Standalone: stop" not in text.partition("\n## Next")[2]:
            report.error(rel, "Next must say that a standalone request stops ('Standalone: stop ...')")
        lines = count_lines(skill_md)
        if name in LANGUAGE_SKILLS:
            missing = [f for f in LANGUAGE_REFERENCES if not os.path.exists(os.path.join(skill_dir, "references", f))]
            if missing:
                report.error(rel, "a language skill needs references/%s" % ", references/".join(missing))
            if not re.search(r"^## Tested versions\b", text, re.M):
                report.error(rel, "a language skill needs a '## Tested versions' section")
            for dirpath, _dirs, filenames in os.walk(skill_dir):
                for filename in filenames:
                    if filename.endswith(".md"):
                        check_size(report, os.path.relpath(os.path.join(dirpath, filename), root), root)
        elif lines > SKILL_LINES:
            report.warn(rel, "SKILL.md has %d lines; aim for under %d" % (lines, SKILL_LINES))
        check_links(report, skill_dir, root)
        check_orphans(report, skill_dir, root, text)
    if total > TOTAL_LIMIT:
        report.error("skills/", "descriptions total %d characters; budget is %d" % (total, TOTAL_LIMIT))
    check_plugin(report, root, skills)
    check_start_commands(report, root)
    check_routing(report, root, descriptions)
    check_single_sources(report, root)
    check_qualified_phrases(report, root)
    check_ledger_values(report, root)
    check_terms(report, root)
    check_shared_skills(report, root)
    check_shared_lines(report, root)
    check_failure_tables(report, root)
    check_launcher_commands(report, root)
    check_pointers(report, root, skills)
    check_title_pointers(report, root, skills)
    check_sh_blocks(report, root)
    report.total_description = total  # type: ignore[attr-defined]
    report.skill_count = len(skills)  # type: ignore[attr-defined]
    return report


def check_single_sources(report: Report, root: str) -> None:
    """A rule stated in a second skill file drifts from the first: point to it instead."""
    for path in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        rel = os.path.relpath(path, root)
        with open(path, encoding="utf-8") as handle:
            text = " ".join(handle.read().split())
        for phrase, (home, pointer) in SINGLE_SOURCES.items():
            if phrase in text and rel != home:
                report.error(rel, "restates %s (%r); point to it instead" % (pointer, phrase))


def check_ledger_values(report: Report, root: str) -> None:
    """A state a skill sets in the Open crashes index is one the ledger's legend lists."""
    path = os.path.join(root, LEDGER)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        section = handle.read().partition("## Open crashes")[2].split("\n## ")[0]
    legend = " ".join(section.split())
    listed = {}
    for column in LEDGER_COLUMNS:
        part = legend.partition(column + ":")[2]
        for other in LEDGER_COLUMNS:
            part = part.split(" %s:" % other)[0]
        listed[column] = set(re.findall(r"`([^`]+)`", part))
    for skill_file in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        with open(skill_file, encoding="utf-8") as handle:
            text = " ".join(handle.read().split())
        for column, value in LEDGER_VALUE_RE.findall(text):
            if value not in listed[column]:
                report.error(os.path.relpath(skill_file, root),
                             "sets %s `%s`, which the Open crashes legend in %s doesn't list" % (column, value, LEDGER))


def check_qualified_phrases(report: Report, root: str) -> None:
    """A rule stated in several places keeps its exception in each (calls on input text are sinks)."""
    for path in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        with open(path, encoding="utf-8") as handle:
            text = " ".join(handle.read().split())
        for phrase, exception in QUALIFIED_PHRASES.items():
            for match in re.finditer(re.escape(phrase), text):
                if exception not in text[match.end():match.end() + 120]:
                    report.error(os.path.relpath(path, root), "states %r without %r" % (phrase, exception))


def check_sh_blocks(report: Report, root: str) -> None:
    """Commands run in any POSIX shell."""
    for path in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        in_sh = False
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if line.strip().startswith("```"):
                    in_sh = not in_sh and line.strip() == "```sh"
                elif in_sh:
                    for match in BRACE_RANGE_RE.finditer(line):
                        report.error("%s:%d" % (os.path.relpath(path, root), number),
                                     "brace expansion %r runs only in bash and zsh: list the arguments" % match.group(0))


def check_pointers(report: Report, root: str, skills: list) -> None:
    """Pointers into other skills name files that exist; script paths resolve."""
    dirs = {os.path.basename(path): path for path in skills}
    copied = None
    init = os.path.join(dirs.get("fuzz-campaign", root), "scripts", "init.py")
    if os.path.exists(init):
        with open(init, encoding="utf-8") as handle:
            match = re.search(r"^SCRIPTS = \(([^)]*)\)", handle.read(), re.M)
        copied = set(re.findall(r'"([^"]+)"', match.group(1))) if match else set()
    for path in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        rel = os.path.relpath(path, root)
        own = next((d for d in skills if path.startswith(d + os.sep)), None)
        with open(path, encoding="utf-8") as handle:
            text = " ".join(handle.read().split())
        for skill, name in POINTER_RE.findall(text):
            base = dirs.get(skill)
            if not base or not any(os.path.exists(os.path.join(base, sub, name)) for sub in ("references", "")):
                report.error(rel, "`%s`'s %s: no such file in that skill" % (skill, name))
        for who, script in SCRIPT_PATH_RE.findall(text):
            base = own if who == "this skill's" else dirs.get(who[: -len(" skill")])
            if not base or not os.path.exists(os.path.normpath(os.path.join(base, script))):
                report.error(rel, "<%s directory>/%s doesn't exist" % (who, script))
        for script in BIN_SCRIPT_RE.findall(text) if copied is not None else ():
            if script not in copied:
                report.error(rel, "bin/%s: init.py doesn't copy it to .seclab/fuzz/bin/" % script)


def normalize_title(title: str) -> str:
    return " ".join(title.split()).rstrip(".,:;").strip().casefold()


def anchors(path: str) -> set:
    """The titles a pointer can name in a file: headings, with and without a trailing parenthetical, and
    the bold titles that start list items and paragraphs (a bold title may wrap onto the next line)."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    found = set()
    fence = None
    for index, line in enumerate(lines):
        match = FENCE_RE.match(line)
        if match:
            fence = match.group(1) if fence is None else (None if match.group(1) == fence else fence)
            continue
        if fence is not None:
            continue
        nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
        match = ANCHOR_RE.match(line + " " + nxt)
        if not match or (match.group(3) and index and lines[index - 1].strip() and not line.lstrip().startswith("#")):
            continue
        title = match.group(1) or match.group(2) or match.group(3)
        if match.group(1):
            title = re.sub(r"^\d+\.\s+", "", line.lstrip("# ").strip())
        title = title.rstrip(".,:; ")
        found.add(normalize_title(title))
        found.add(normalize_title(re.sub(r"\s*\([^)]*\)$", "", title)))
        found.add(normalize_title(title.split(", ")[0]))
    return found


def check_title_pointers(report: Report, root: str, skills: list) -> None:
    """Pointers into another file name a title that file has, never a step or check number."""
    dirs = {os.path.basename(path): path for path in skills}
    cache = {}
    for path in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        rel = os.path.relpath(path, root)
        own = next((d for d in skills if path.startswith(d + os.sep)), None)
        with open(path, encoding="utf-8") as handle:
            text = " ".join(strip_fences(handle.read()).split())
        for match in NUMBERED_POINTER_RE.finditer(text):
            report.error(rel, "%r: point into another file by the step's title, not its number" % match.group(0))
        for match in UNCHECKED_GENERIC_RE.finditer(text):
            report.error(rel, "%r: name the language skill's file (and title), e.g. \"the language skill's run.md\""
                         % match.group(0))
        for match in GENERIC_POINTER_RE.finditer(text):
            name, titles = match.group(1), match.group(2) or ""
            for lang in sorted(LANGUAGE_SKILLS):
                base = dirs.get(lang)
                if base is None:
                    continue
                target = next((os.path.join(base, sub, name) for sub in ("references", "")
                               if os.path.exists(os.path.join(base, sub, name))), None)
                if target is None:
                    report.error(rel, "%r: %s has no %s" % (match.group(0), lang, name))
                    continue
                if target not in cache:
                    cache[target] = anchors(target)
                for title in re.findall(r'"([^"]+)"', titles):
                    if normalize_title(title) not in cache[target]:
                        report.error(rel, "%r: %s has no heading or step titled %r"
                                     % (match.group(0), os.path.relpath(target, root), title))
        text = GENERIC_POINTER_RE.sub(" ", text)
        for match in TITLE_POINTER_RE.finditer(text):
            skill, name, titles = match.group(1), match.group(2), match.group(3)
            if skill:
                base, name = dirs.get(skill), name or "SKILL.md"
            else:
                base, name, titles = own, match.group(4) or match.group(5), match.group(6)
            target = None
            for sub in ("references", ""):
                candidate = os.path.join(base, sub, name) if base else None
                if candidate and os.path.exists(candidate):
                    target = candidate
                    break
            if target is None:
                report.error(rel, "%r: no such file%s" % (match.group(0), " in " + skill if skill else " next to it"))
                continue
            if target not in cache:
                cache[target] = anchors(target)
            for title in re.findall(r'"([^"]+)"', titles):
                if normalize_title(title) not in cache[target]:
                    report.error(rel, "%r: %s has no heading or step titled %r"
                                 % (match.group(0), os.path.relpath(target, root), title))


def strip_fences(text: str) -> str:
    """Remove fenced code blocks; inline code stays, since pointers quote file names in it."""
    kept = []
    fence = None
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if match:
            fence = match.group(1) if fence is None else (None if match.group(1) == fence else fence)
            continue
        if fence is None:
            kept.append(line)
    return "\n".join(kept)


def check_terms(report: Report, root: str) -> None:
    for path in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        with open(path, encoding="utf-8") as handle:
            text = " ".join(re.sub(r"<!--.*?-->", "", handle.read(), flags=re.S).split())
        for pattern, instead in TERMS.items():
            for match in re.finditer(pattern, text, re.I):
                report.error(os.path.relpath(path, root), "%r: say %s" % (match.group(0), instead))


def check_launcher_commands(report: Report, root: str) -> None:
    """Launcher commands use $L from .seclab/fuzz/env.sh, which every command sources."""
    for path in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                match = OWN_LAUNCHER_RE.search(line)
                if match:
                    report.error("%s:%d" % (os.path.relpath(path, root), number),
                                 "%r: use $L from .seclab/fuzz/env.sh (python3 $F/bin/launcher.py)" % match.group(0))


def check_shared_lines(report: Report, root: str) -> None:
    """Lines several skills state alike (the Jazzer status, the workflow skills' "Run every step")."""
    for prefix, paths in SHARED_LINES:
        present = [rel for rel in paths if os.path.exists(os.path.join(root, rel))]
        if len(present) < 2:
            continue
        first = None
        for rel in present:
            path = os.path.join(root, rel)
            with open(path, encoding="utf-8") as handle:
                found = [line for line in handle.read().splitlines() if line.startswith(prefix)]
            if len(found) != 1:
                report.error(rel, "needs exactly one %s line, has %d" % (prefix, len(found)))
            elif first is None:
                first = (rel, found[0])
            elif found[0] != first[1]:
                report.error(rel, "its %s line differs from %s's" % (prefix, first[0]))


def check_failure_tables(report: Report, root: str) -> None:
    """A symptom table is the "Common failures" section that ends its reference."""
    for path in sorted(glob.glob(os.path.join(root, "skills", "**", "*.md"), recursive=True)):
        rel = os.path.relpath(path, root)
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        section, fence, table_at = None, None, None
        for number, line in enumerate(lines, 1):
            match = FENCE_RE.match(line)
            if match:
                fence = match.group(1) if fence is None else (None if match.group(1) == fence else fence)
                continue
            if fence is not None:
                continue
            if line.startswith("## "):
                if table_at is not None:
                    report.error("%s:%d" % (rel, table_at), "'Common failures' must be the last section of the file")
                    table_at = None
                section = line[3:].strip()
            elif re.match(r"\|\s*Symptom\s*\|", line):
                if section != "Common failures":
                    report.error("%s:%d" % (rel, number), "a symptom table belongs under '## Common failures' at the end "
                                 "of the reference, not under %r" % section)
                else:
                    table_at = number


def check_shared_skills(report: Report, root: str) -> None:
    """Shared skills serve topics other than fuzzing: they never use the fuzzing workspace."""
    for path in sorted(glob.glob(os.path.join(root, "skills", "shared", "**", "*.md"), recursive=True)):
        with open(path, encoding="utf-8") as handle:
            if ".seclab/fuzz" in handle.read():
                report.error(os.path.relpath(path, root), "a shared skill uses .seclab/fuzz; that belongs to the fuzzing skills")


def check_start_commands(report: Report, root: str) -> None:
    """The skills never push or post; the documented agent commands keep the deny rules as a guard."""
    for name in START_COMMAND_FILES:
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                for match in START_COMMAND_RE.finditer(line):
                    rules = DENY_RULES[match.group(1)]
                    missing = [command for command, rule in rules.items() if rule not in match.group(2)]
                    if missing:
                        report.error("%s:%d" % (name, number), "%s start command doesn't deny %s"
                                     % (match.group(1), ", ".join(missing)))


def check_routing(report: Report, root: str, descriptions: dict) -> None:
    """An agent picks a skill by its description: the README's example requests must be in it."""
    path = os.path.join(root, "README.md")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            match = ROUTING_ROW_RE.match(line)
            if not match or match.group(1) not in descriptions:
                continue
            description = " ".join(descriptions[match.group(1)].lower().split())
            for phrase in re.findall(r'"([^"]+)"', match.group(2)):
                key = phrase.split("`")[0].strip().rstrip("?.!").lower()
                if key and key not in description:
                    report.error("README.md:%d" % number, "routes %r to %s, whose description doesn't contain it"
                                 % (key, match.group(1)))


def check_plugin(report: Report, root: str, skills: list) -> None:
    path = os.path.join(root, ".claude-plugin", "plugin.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError as error:
        report.error(".claude-plugin/plugin.json", "invalid JSON: %s" % error)
        return
    listed = data.get("skills", [])
    if isinstance(listed, str):
        listed = [listed]
    listed_dirs = {os.path.normpath(os.path.join(root, entry)) for entry in listed}
    actual = {os.path.normpath(skill) for skill in skills}
    for missing in sorted(actual - listed_dirs):
        report.error(".claude-plugin/plugin.json", "skill not listed: %s" % os.path.relpath(missing, root))
    for extra in sorted(listed_dirs - actual):
        report.error(".claude-plugin/plugin.json", "listed path has no SKILL.md: %s" % os.path.relpath(extra, root))
    market = os.path.join(root, ".claude-plugin", "marketplace.json")
    if os.path.exists(market):
        with open(market, encoding="utf-8") as handle:
            entries = [p for p in json.load(handle).get("plugins", []) if p.get("name") == data.get("name")]
        for entry in entries:
            for key in ("keywords", "description", "version"):
                if key in entry and entry[key] != data.get(key):
                    report.error(".claude-plugin/marketplace.json", "the %s entry's %s differs from plugin.json's" % (entry["name"], key))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = argv[0] if argv else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report = check(root)
    for warning in report.warnings:
        print("warning: " + warning)
    for error in report.errors:
        print("error: " + error)
    count = getattr(report, "skill_count", 0)
    total = getattr(report, "total_description", 0)
    print(
        "%d skills checked; descriptions total %d/%d characters; %d errors, %d warnings"
        % (count, total, TOTAL_LIMIT, len(report.errors), len(report.warnings))
    )
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
