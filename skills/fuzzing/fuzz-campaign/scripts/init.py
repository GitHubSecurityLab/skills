#!/usr/bin/env python3
"""Set up or refresh the fuzzing workspace in .seclab/fuzz/ of a git repository.

Every fuzzing skill runs it first, from the repository root, by its path in
the installed plugin (the agent shows each skill's directory):

    python3 <fuzz-campaign skill directory>/scripts/init.py
        [--branch new|current [--base <ref>]] [--notice-shown] [--disposable]

It is idempotent. Each run:
  - refuses Windows and Python before 3.9, with a clear error;
  - checks that the current directory is inside a git repository, and warns
    about a repository path that libFuzzer's merges can't use;
  - refuses a repository that tracks files under .seclab/, in any case
    (macOS's file system ignores it): their ledger, threat model and Next
    actions would come from the repository, not from the user; and refuses
    a .seclab/ that is a symbolic link, or whose fuzz/, env.sh, ledger or
    threat model is;
  - creates .seclab/fuzz/ and its directories (bin, build, targets, sinks,
    corpus, dict, coverage, crashes, reports: an agent's file tools may not
    create parents), and adds .seclab/ and .cifuzz-corpus/ to git's exclude
    file (never to .gitignore); a read-only .git is an error that asks for
    the user's approval;
  - writes .seclab/fuzz/env.sh, which every command sources (an agent's
    commands don't keep shell variables), with a line that makes zsh split
    unquoted variables like bash, F and L kept current, and the language
    skill's environment block below them;
  - copies launcher.py, coverage.py, crash_buckets.py and reach.py into
    .seclab/fuzz/bin/, and copies them again whenever the plugin's copy
    differs (after a plugin update);
  - prints the first-build notice until --notice-shown records it, and the
    safety rules from fuzz-campaign's SKILL.md. --notice-shown and
    --disposable are recorded in git's directory (git rev-parse --git-path
    seclab), which a clone or a copied .seclab/ doesn't carry, and the
    ledger's two lines are rewritten from there on every run;
  - with a ledger, refuses --branch and --base: the branch and base are set
    (a standalone ledger moves to a new branch with --branch new only once,
    before its first commit, uncommitted work included); and refuses a
    checkout on another branch, or one whose history lacks the base commit:
    the user decides;
  - without a ledger (.seclab/fuzz/CAMPAIGN.md): with --branch, switches to a
    new seclab/fuzz-<date> branch at the base commit (new; --base, default
    HEAD) or stays (current; --base, a commit the branch builds on, default
    HEAD), prints the base and how far it is behind the default branch of
    upstream (a fork's clone) or origin, and writes the ledger from the
    template in fuzz-campaign's references/ledger.md, with status
    "standalone"; without
    --branch, exits 2. Untracked files never count as changes; uncommitted
    changes block only --branch new.

Exit status: 0 ready; 2 the ledger needs --branch; 1 error, usage errors
included.
"""

from __future__ import annotations

import argparse
import datetime
import filecmp
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SCRIPTS = ("launcher.py", "coverage.py", "crash_buckets.py", "reach.py")
EXCLUDES = (".seclab/", ".cifuzz-corpus/")
SUBDIRS = ("bin", "build", "targets", "sinks", "corpus", "dict", "coverage", "crashes", "reports")
LEDGER = os.path.join(".seclab", "fuzz", "CAMPAIGN.md")
ENV_FILE = os.path.join(".seclab", "fuzz", "env.sh")
WORKSPACE_LINKS = (".seclab", os.path.join(".seclab", "fuzz"), ENV_FILE, LEDGER, os.path.join(".seclab", "threat-model.md"))
ENV_HEADER = """\
# Every command starts with `. .seclab/fuzz/env.sh;`: an agent's commands
# don't keep shell variables. init.py keeps F and L up to date; the language
# skill's environment block goes below them.
"""
# The commands rely on bash's word splitting of unquoted variables ($L, $JOPTS, $HARDEN), which zsh,
# Claude Code's and Codex's shell on macOS, doesn't do by default.
ZSH_WORD_SPLIT = '[ -z "${ZSH_VERSION-}" ] || setopt sh_word_split'
# Characters that break libFuzzer's -merge and -minimize_crash, which re-run
# their command through the shell without quoting (see launcher.py).
SHELL_UNSAFE = re.compile(r"[\s'\"\\$`!*?;&|<>(){}\[\]#~]")

NOTICE = """\
FIRST-BUILD NOTICE (show it to the user before any build or run of project code):
  Fuzzing builds and runs this project's code: its build scripts, its tests,
  and the fuzzed code with millions of mutated inputs. It runs natively with
  your permissions and isn't confined: it can read your files, credentials
  included, write to absolute paths and use the network, and its memory and
  disk use aren't fully capped. Use a codespace or VM for code you don't
  trust.
  Interactive mode on the user's own machine: ask once to continue.
  Codespace or VM: show it, no question. Autonomous mode: a warning at the start.
  Then run init.py again with --notice-shown."""


class InitError(Exception):
    pass


def find_plugin_root():
    """The directory whose .claude-plugin/plugin.json lists this skill; None for a copy outside the plugin."""
    skill = os.path.realpath(SKILL_DIR)
    path = os.path.dirname(skill)
    while True:
        try:
            with open(os.path.join(path, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
                listed = json.load(handle).get("skills", [])
        except (OSError, ValueError, AttributeError):
            listed = []
        for entry in [listed] if isinstance(listed, str) else listed:
            if isinstance(entry, str) and os.path.realpath(os.path.join(path, entry)) == skill:
                return path
        if os.path.dirname(path) == path:
            return None
        path = os.path.dirname(path)


PLUGIN_ROOT = find_plugin_root()


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise InitError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout.strip() if result.returncode == 0 else ""


def plugin_version() -> str:
    if PLUGIN_ROOT is None:
        return "unknown"
    try:
        with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            return str(json.load(handle).get("version", "unknown"))
    except (OSError, ValueError):
        return "unknown"


def ensure_excludes() -> None:
    path = git("rev-parse", "--git-path", "info/exclude")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        present = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                present = {line.strip() for line in handle}
        missing = [entry for entry in EXCLUDES if entry not in present]
        if missing:
            with open(path, "a+", encoding="utf-8") as handle:
                handle.seek(0)
                text = handle.read()
                if text and not text.endswith("\n"):
                    handle.write("\n")
                handle.write("".join(entry + "\n" for entry in missing))
    except OSError as error:
        raise read_only_git(path, error, "which keeps .seclab/ out of git") from None


def read_only_git(path: str, error, purpose: str) -> InitError:
    return InitError(
        "can't write %s (%s), %s: .git is read-only here (Codex's default sandbox makes it so). Ask the "
        "user to approve running init.py with write access to .git (--branch new also creates a branch "
        "there), then run it again." % (path, getattr(error, "strerror", None) or error, purpose)
    )


def switch(*args: str) -> None:
    """git switch; a failure in a read-only .git gets the same hint as init.py's other writes there."""
    result = subprocess.run(["git", "switch", "-q"] + list(args), capture_output=True, text=True)
    if result.returncode == 0:
        return
    heads = git("rev-parse", "--git-path", "refs/heads")
    probe = os.path.join(heads, ".seclab-write-test")
    try:
        os.close(os.open(probe, os.O_CREAT | os.O_WRONLY))
        os.remove(probe)
    except OSError:
        raise read_only_git(heads, result.stderr.strip(), "where the new branch goes") from None
    raise InitError("git switch %s failed: %s" % (" ".join(args), result.stderr.strip()))


def refuse_foreign_workspace() -> None:
    # In any case: macOS's default file system ignores it, so a tracked .SECLAB/ is .seclab/ there.
    tracked = git("ls-files", "--", ":(icase).seclab").splitlines()
    if tracked:
        raise InitError(
            "git tracks %s%s: a workspace that comes with the repository can't be trusted (it can mark the "
            "first-build notice shown, declare the environment disposable or plant Next actions). Tell the "
            "user; fuzz a checkout that doesn't track .seclab/, and never delete or untrack these files "
            "yourself." % (tracked[0], " and %d more" % (len(tracked) - 1) if len(tracked) > 1 else "")
        )
    for path in WORKSPACE_LINKS:
        if os.path.islink(path):
            raise InitError(
                "%s is a symbolic link (to %s): a workspace that points elsewhere can't be trusted, since its "
                "files may come from the repository. Tell the user; never delete or change it yourself."
                % (path, os.readlink(path))
            )


class State:
    """The notice and disposable flags, kept in git's directory: a clone or a copied .seclab/ doesn't carry them."""

    def __init__(self) -> None:
        self.dir = git("rev-parse", "--git-path", "seclab")

    def read(self, name: str) -> str:
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as handle:
                return handle.read().strip() or "unknown date"
        except OSError:
            return ""

    def record(self, name: str) -> None:
        path = os.path.join(self.dir, name)
        if os.path.exists(path):
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(datetime.date.today().isoformat() + "\n")
        except OSError as error:
            raise read_only_git(path, error, "which records the user's answers outside .seclab/") from None

    def notice(self) -> str:
        shown = self.read("notice-shown")
        return "shown %s" % shown if shown else "not yet shown"

    def environment(self) -> str:
        if os.environ.get("CODESPACES") == "true":
            return "yes (detected: CODESPACES)"
        return "yes (declared)" if self.read("disposable") else "no"


def write_env_file() -> None:
    values = {"F": shlex.quote(os.path.join(os.getcwd(), ".seclab", "fuzz")), "L": '"python3 $F/bin/launcher.py"'}
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    else:
        lines = ENV_HEADER.splitlines() + [ZSH_WORD_SPLIT, "F=", "L="]
    after = 0
    for key, value in values.items():
        for index, line in enumerate(lines):
            if line.startswith(key + "="):
                lines[index] = "%s=%s" % (key, value)
                after = index + 1
                break
        else:
            # L uses F, so a missing line goes below the one before it.
            lines.insert(after, "%s=%s" % (key, value))
            after += 1
    if ZSH_WORD_SPLIT not in lines:
        lines.insert(next(i for i, line in enumerate(lines) if line.startswith("F=")), ZSH_WORD_SPLIT)
    with open(ENV_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def sync_scripts() -> str:
    bindir = os.path.join(".seclab", "fuzz", "bin")
    copied = []
    for name in SCRIPTS:
        src, dst = os.path.join(HERE, name), os.path.join(bindir, name)
        if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
            shutil.copy2(src, dst)
            copied.append(name)
    if not copied:
        return "up to date"
    return "copied " + ", ".join(copied)


def section(text: str, heading: str) -> str:
    """The body of a `## heading` section of a Markdown file."""
    match = re.search(r"^## %s\s*$(.*?)(?=^## |\Z)" % re.escape(heading), text, re.M | re.S)
    if not match:
        raise InitError("section %r not found" % heading)
    return match.group(1).strip("\n")


def ledger_template() -> str:
    r"""The fenced template right after `## \`CAMPAIGN.md\`` in references/ledger.md."""
    with open(os.path.join(SKILL_DIR, "references", "ledger.md"), encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r"^## `CAMPAIGN\.md`\s*\n+```markdown\n(.*?)^```\s*$", text, re.M | re.S)
    if not match:
        raise InitError("no ```markdown template under ## `CAMPAIGN.md` in references/ledger.md")
    return match.group(1)


def fill_template(template: str, values: dict) -> str:
    out, in_settings, after_separator = [], False, False
    for line in template.splitlines():
        if line.startswith("## "):
            in_settings = line.strip() == "## Settings"
            after_separator = False
        elif line.startswith("|"):
            if re.match(r"^\|(\s*-+\s*\|)+\s*$", line):
                after_separator = True
            elif after_separator and not in_settings:
                continue  # example rows of the template's tables
            line = line.replace("<percent of branches in combined scope, or none>", "none")
        elif line.startswith("1. <"):
            continue
        for key, value in values.items():
            if line.startswith(key):
                line = key + " " + value
        out.append(line)
    return "\n".join(out) + "\n"


def head_ref() -> str:
    """The checkout's branch, else the tag at HEAD, else "detached"."""
    return git("symbolic-ref", "--short", "-q", "HEAD", check=False) or git(
        "describe", "--tags", "--exact-match", check=False
    ) or "detached"


def describe_base(base: str, ref: str) -> str:
    """`<sha> (<ref>)`, and how far it is behind the project's default branch (upstream's in a fork's clone).
    Older git records no upstream/HEAD on fetch (only `remote set-head` does): then upstream's branch that
    origin/HEAD names."""
    text = "%s (%s)" % (base, ref)
    origin = git("symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD", check=False)
    default = git("symbolic-ref", "--short", "-q", "refs/remotes/upstream/HEAD", check=False)
    if not default and origin:
        candidate = "upstream/" + origin.split("/", 1)[1]
        if git("rev-parse", "--verify", "--quiet", "refs/remotes/" + candidate, check=False):
            default = candidate
    default = default or origin
    if default:
        behind = git("rev-list", "--count", "%s..%s" % (base, default), check=False)
        if behind and behind != "0":
            text += ", %s commit%s behind %s" % (behind, "" if behind == "1" else "s", default)
    return text


def check_checkout() -> None:
    """The checkout must be where the ledger says the work is."""
    branch, base = ledger_line("- **Branch:**"), ledger_line("- **Base commit:**").split(" ", 1)[0]
    current = git("symbolic-ref", "--short", "-q", "HEAD", check=False) or "detached"
    problem = None
    if branch != current:
        problem = "the checkout is on %s, but the ledger records branch %s" % (current, branch)
    elif base and subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], capture_output=True).returncode:
        problem = "the ledger's base commit %s isn't in the checkout's history (rebased or reset?)" % base
    if problem:
        raise InitError(
            "%s, and the campaign's work is there. Don't change the ledger or the branch yourself: ask the user "
            "whether to switch back (git switch %s) or to start a new campaign from here (fuzz-campaign, "
            "analyze.md: a new release)." % (problem, branch)
        )


def new_branch_name() -> str:
    today = datetime.date.today().isoformat()
    name, n = "seclab/fuzz-%s" % today, 1
    while git("rev-parse", "--verify", "--quiet", "refs/heads/" + name, check=False):
        n += 1
        name = "seclab/fuzz-%s-%d" % (today, n)
    return name


def movable(args: argparse.Namespace) -> bool:
    """A standalone ledger's work moves to a new branch only once, before its first commit."""
    return (
        args.branch == "new"
        and not args.base
        and ledger_line("- **Status:**") == "standalone"
        and not ledger_line("- **Branch:**").startswith("seclab/fuzz-")
        and ledger_line("- **Base commit:**").split(" ", 1)[0] == git("rev-parse", "HEAD")
    )


def write_ledger(branch_choice: str, base_choice: str, state: State) -> tuple:
    template = ledger_template()
    if branch_choice == "new" and git("status", "--porcelain", "--untracked-files=no"):
        raise InitError(
            "the working tree has uncommitted changes: ask the user to commit or stash them, to use "
            "a separate worktree, or to stay on the current branch (--branch current); never discard them. "
            "Then run init.py again."
        )
    if base_choice:
        base = git("rev-parse", "--verify", "--quiet", base_choice + "^{commit}", check=False)
        if not base:
            raise InitError("--base %s is not a commit, tag or branch here" % base_choice)
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], capture_output=True)
        if branch_choice == "current" and ancestor.returncode != 0:
            raise InitError(
                "--base %s isn't an ancestor of the current branch; with --branch current the base is a commit "
                "the branch builds on. Use --branch new for another base" % base_choice
            )
        base_ref = base_choice
    else:
        base, base_ref = git("rev-parse", "HEAD"), head_ref()
    today = datetime.date.today().isoformat()
    if branch_choice == "new":
        switch("-c", new_branch_name(), base)
    branch = git("symbolic-ref", "--short", "-q", "HEAD", check=False) or "detached"
    values = {
        "# Fuzzing campaign:": os.path.basename(os.getcwd()),
        "- **Status:**": "standalone",
        "- **Base commit:**": "%s (%s), chosen %s" % (base, base_ref, today),
        "- **Branch:**": branch,
        "- **Plugin:**": "seclab-skills %s; scripts in .seclab/fuzz/bin/" % plugin_version(),
        "- **First-build notice:**": state.notice(),
        "- **Disposable environment:**": state.environment(),
        "- Started:": "%s · Combined scope branch coverage: none yet (goal none)" % today,
    }
    text = fill_template(template, values)
    with open(LEDGER, "w", encoding="utf-8") as handle:
        handle.write(text)
    decisions = os.path.join(".seclab", "fuzz", "decisions.md")
    if not os.path.exists(decisions):
        open(decisions, "w", encoding="utf-8").close()
    return branch, describe_base(base, base_ref)


def update_ledger_line(key: str, value: str) -> None:
    with open(LEDGER, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    for index, line in enumerate(lines):
        if line.startswith(key):
            lines[index] = key + " " + value
    with open(LEDGER, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def ledger_line(key: str) -> str:
    with open(LEDGER, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(key):
                return line[len(key) :].strip()
    return ""


def safety_rules() -> str:
    with open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8") as handle:
        return section(handle.read(), "Safety rules (every step)")


def run(args: argparse.Namespace) -> int:
    root = git("rev-parse", "--show-toplevel", check=False)
    if not root:
        raise InitError(
            "not inside a git repository. Ask the user before running "
            "`git init && git add -A && git commit -m 'Import source'`, and note in decisions.md "
            "that past-fix history is unavailable. Then run init.py again."
        )
    os.chdir(root)
    if PLUGIN_ROOT and os.path.realpath(os.getcwd()) == os.path.realpath(PLUGIN_ROOT):
        raise InitError("run init.py from the repository to fuzz, not from the plugin")
    refuse_foreign_workspace()
    print("repository: %s" % root)
    bad = SHELL_UNSAFE.search(root)
    if bad:
        print(
            "warning: the repository path contains %r: libFuzzer's merges and minimizing can't run "
            "there (the launcher refuses them). Tell the user; the checkout should move." % bad.group(0)
        )
    for sub in SUBDIRS:
        os.makedirs(os.path.join(".seclab", "fuzz", sub), exist_ok=True)
    ensure_excludes()
    write_env_file()
    version = plugin_version()
    print("scripts: %s (seclab-skills %s) in .seclab/fuzz/bin/" % (sync_scripts(), version))

    status = 0
    if os.path.exists(LEDGER) and args.branch and not movable(args):
        raise InitError(
            "the ledger already records the branch (%s) and base commit (%s), and earlier work is on that "
            "branch; they don't change here (a standalone ledger moves to a new branch only once, before its "
            "first commit). Run init.py again without --branch and --base. To work from another base, start a new "
            "campaign (fuzz-campaign, analyze.md: a new release)."
            % (ledger_line("- **Branch:**"), ledger_line("- **Base commit:**"))
        )
    state = State()
    if args.notice_shown:
        state.record("notice-shown")
    if args.disposable:
        state.record("disposable")
    if os.path.exists(LEDGER):
        check_checkout()
        if args.branch:
            name = new_branch_name()
            switch("-c", name)  # uncommitted work comes along
            update_ledger_line("- **Branch:**", name)
            print("branch: moved to %s; the base stays %s" % (name, ledger_line("- **Base commit:**")))
        update_ledger_line("- **First-build notice:**", state.notice())
        update_ledger_line("- **Disposable environment:**", state.environment())
        update_ledger_line("- **Plugin:**", "seclab-skills %s; scripts in .seclab/fuzz/bin/" % version)
        print("ledger: %s (status %s)" % (LEDGER, ledger_line("- **Status:**")))
    elif args.branch:
        branch, base = write_ledger(args.branch, args.base, state)
        print("ledger: %s written (status standalone, branch %s)" % (LEDGER, branch))
        print("base: %s" % base)
    else:
        print("base: %s" % describe_base(git("rev-parse", "HEAD"), head_ref()))
        print(
            "ledger: missing. Run init.py again with --branch new or --branch current: a campaign's "
            "setup card chooses (new is the default, and autonomous mode's choice); a single-step request "
            "uses --branch current and, before its first commit, asks whether the work moves to a new "
            "seclab/fuzz-<date> branch (init.py --branch new)."
        )
        status = 2

    if not state.read("notice-shown"):
        print("\n" + NOTICE)
    print("\nSAFETY RULES (every step):\n" + safety_rules())
    return status


class Parser(argparse.ArgumentParser):
    """Usage errors exit 1: exit 2 means the ledger needs --branch."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(1, "%s: error: %s\n" % (self.prog, message))


def platform_error(platform: str, version) -> str:
    """Why the fuzzing skills can't run here, or "" if they can."""
    if platform == "win32":
        return ("the fuzzing skills run on macOS and Linux, not natively on Windows (the launcher needs POSIX "
                "process groups): use WSL2, a VM or a codespace")
    if tuple(version[:2]) < (3, 9):
        return "the fuzzing skills need Python 3.9 or later, not %d.%d" % tuple(version[:2])
    return ""


def main(argv=None) -> int:
    problem = platform_error(sys.platform, sys.version_info)
    if problem:
        print("error: %s" % problem, file=sys.stderr)
        return 1
    parser = Parser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--branch", choices=("new", "current"), help="where changes go, when there's no ledger yet")
    parser.add_argument("--base", help="the campaign's base commit, tag or branch (default HEAD; with current, an ancestor of HEAD)")
    parser.add_argument("--notice-shown", action="store_true", help="record that the first-build notice was shown")
    parser.add_argument("--disposable", action="store_true", help="the user declared this environment disposable")
    args = parser.parse_args(argv)
    if args.base and not args.branch:
        parser.error("--base needs --branch")
    try:
        return run(args)
    except InitError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
