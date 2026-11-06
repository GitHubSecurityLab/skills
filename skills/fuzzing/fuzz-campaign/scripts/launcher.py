#!/usr/bin/env python3
"""Run fuzzing commands, replays and reproductions for a campaign.

Every run of project code goes through this launcher. It runs the command it
is given (it never decides what to run), as one or more copies, each with:
  - an empty environment plus PATH, LANG, --env values and SECLAB_RUN
    (which marks its processes as the copy's);
  - its own scratch directory as working directory, HOME and TMPDIR inside
    it (and JAVA_TOOL_OPTIONS pointing Java there);
  - stdin from /dev/null, its own process group, and a bounded log.

Commands:
  start   start a run (foreground by default; --detach for a supervisor;
          --prepare only writes its spec.json and prints the start command)
  wait    wait for a run to end, at most --max seconds
  status  list runs, or show one
  stop    stop a run

Runs live in <workspace>/runs/<id>/: spec.json (what to run, placeholders
unresolved), meta.json (trees, times, copies, states, findings),
progress.tsv (samples at the start, every 5 minutes and at the end),
artifacts/copy-<i>/ (findings), copy-<i>/log and copy-<i>/work/ (scratch).

Placeholders the launcher replaces in commands, --env values, --findings and
--corpus: {run}, {scratch}, {tmp}, {copy}. Other braces are left alone.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Tunables (module constants so tests can shorten them)

POLL_SECONDS = 0.5
STOP_GRACE_SECONDS = 10.0  # TERM, then KILL after this long
DEADLINE_GRACE_SECONDS = 5.0  # a timed run's deadline is --time plus at least this
JVM_GRACE_SECONDS = 15.0  # both graces for a JVM, which starts and stops slowly
PROGRESS_SECONDS = 300.0  # progress sample and foreground progress line
LOG_CHECK_SECONDS = 5.0
LOG_MAX_BYTES = 64 * 1024 * 1024  # trim a copy's log above this size ...
LOG_HEAD_BYTES = 4 * 1024 * 1024  # ... keeping its head
LOG_TAIL_BYTES = 16 * 1024 * 1024  # ... and its tail
DETACH_WAIT_SECONDS = 20.0

PLACEHOLDER = re.compile(r"\{(run|scratch|tmp|copy)\}")
JVM_COMMAND = re.compile(r"(^|[\s/])(java|mvnw?|gradlew?)(\s|$)")
NOT_FINDINGS = re.compile(r"^(README\.txt|slow-unit-.*|\..*)$")
# libFuzzer's status lines (fork mode: "#<n>: cov: ..."), not sanitizer
# stack frames ("#11 0x... in ..."), and AFL++'s.
STATUS_LINE = re.compile(r"^(#\d+\s+(INITED|NEW|REDUCE|pulse|DONE|RELOAD)\b|#\d+: cov: |\[\*\]\s|\[\+\]\s|\[!\]\s)")
ANSI_CODE = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)|[@-Z\\-_])")
LONG_RUN_ID = re.compile(r"-long\d+$")
RUN_MARK = "SECLAB_RUN"  # in each copy's environment: <run id>/<copy>/<random>, see owned_member
# A process identity that can't be read here (a sandbox): never taken for "exited".
UNKNOWN = "unknown"

EXIT_USAGE = 2
EXIT_CPU = 3
EXIT_TIMED_OUT = 124
EXIT_CANT_START = 127


class LauncherError(Exception):
    """A usage problem, reported without a traceback."""


class CantStart(Exception):
    """A copy's command couldn't be started (exit 127)."""


# --------------------------------------------------------------------------
# Small helpers


def now() -> float:
    """The wall clock: for times that are shown or stored, and elapsed times
    read by another process (status, wait)."""
    return time.time()


def awake() -> float:
    """A clock that doesn't jump with the wall clock and doesn't advance
    while the machine sleeps (nor do the copies run then): for deadlines,
    timeouts and waits within one process."""
    return time.monotonic()


def iso(ts) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def say(text: str = "", stream=None) -> None:
    """Print, ignoring write errors (e.g. after the terminal hung up)."""
    try:
        print(text, file=stream or sys.stdout, flush=True)
    except (OSError, ValueError):
        pass


def read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, data) -> None:
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def duration(seconds) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%dh%02dm" % (hours, minutes)
    if minutes:
        return "%dm%02ds" % (minutes, secs)
    return "%ds" % secs


def dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%d %s" % (size, unit) if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%d B" % size


def cpu_count() -> int:
    """The cores this process may use: the machine's, limited by its CPU
    affinity (taskset) and a cgroup quota (a container's --cpus)."""
    count = os.cpu_count() or 1
    if hasattr(os, "sched_getaffinity"):
        count = min(count, len(os.sched_getaffinity(0)) or count)
    quota = cgroup_cpus()
    if quota:
        count = min(count, max(1, math.ceil(quota)))
    return count


def cgroup_cpus(root: str = "/sys/fs/cgroup", own: str = "/proc/self/cgroup"):
    """The smallest CPU quota, in cores, of this process's cgroup and its
    parents (v2 cpu.max, v1 CFS quota), or None."""
    try:
        with open(own, encoding="utf-8") as handle:
            entries = [line.rstrip("\n").split(":", 2) for line in handle]
    except OSError:
        return None
    quotas = []
    for entry in entries:
        if len(entry) != 3 or not (entry[1] == "" or "cpu" in entry[1].split(",")):
            continue
        v2 = entry[1] == ""
        base = root if v2 else os.path.join(root, "cpu")
        parts = [part for part in entry[2].split("/") if part]
        # A container sees its own cgroup at the root, under a path from the host's view.
        for depth in range(len(parts), -1, -1):
            directory = os.path.join(base, *parts[:depth])
            try:
                if v2:
                    with open(os.path.join(directory, "cpu.max"), encoding="utf-8") as handle:
                        quota, period = handle.read().split()[:2]
                else:
                    with open(os.path.join(directory, "cpu.cfs_quota_us"), encoding="utf-8") as handle:
                        quota = handle.read()
                    with open(os.path.join(directory, "cpu.cfs_period_us"), encoding="utf-8") as handle:
                        period = handle.read()
                quota, period = int(quota), int(period)  # "max": no quota
            except (OSError, ValueError):
                continue
            if quota > 0 and period > 0:
                quotas.append(quota / period)
    return min(quotas) if quotas else None


# --------------------------------------------------------------------------
# Processes


def process_identity(pid):
    """A string that identifies one process across PID reuse (its start
    time); None if there is no such process; UNKNOWN if it can't be read
    here. A zombie has exited: where PID 1 doesn't reap orphans (a
    container's `sleep`), killed copies stay zombies."""
    if not pid:
        return None
    stat = "/proc/%d/stat" % pid
    if os.path.exists("/proc/self/stat"):
        try:
            with open(stat, encoding="utf-8", errors="replace") as handle:
                fields = handle.read().rsplit(")", 1)[1].split()
            if fields[0] in ("Z", "X"):
                return None
            return "proc:" + fields[19]
        except (OSError, IndexError):
            return None
    if sys.platform != "darwin":
        return UNKNOWN
    # macOS: sysctl(KERN_PROC_PID), not `ps`, which is setuid and which
    # sandboxes (Codex's Seatbelt profile) can't run; struct kinfo_proc
    # starts with extern_proc: p_starttime at 0, p_stat at 36, p_pid at 40.
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                                ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN, KERN_PROC, KERN_PROC_PID
        buf = ctypes.create_string_buffer(1024)
        size = ctypes.c_size_t(len(buf))
        if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0:
            return UNKNOWN
    except (ImportError, OSError, AttributeError):
        return UNKNOWN
    if size.value == 0:
        return None
    raw = buf.raw[: size.value]
    if len(raw) < 44 or struct.unpack_from("=i", raw, 40)[0] != pid:
        return UNKNOWN
    if raw[36] == 5:  # SZOMB
        return None
    seconds, micros = struct.unpack_from("=qi", raw, 0)
    return "sysctl:%d.%06d" % (seconds, micros)


def liveness(pid, identity):
    """True if `pid` is still the process that was recorded, False if that
    process has exited, None if that can't be told here."""
    if not pid or identity is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    current = process_identity(pid)
    if current is None:
        return False
    if UNKNOWN in (current, identity):
        return None
    return current == identity


def is_alive(pid, identity) -> bool:
    """True only if `pid` is verifiably still the process that was recorded."""
    return liveness(pid, identity) is True


def signal_group(pid, identity, sig) -> bool:
    """Signal a copy's process group, but never a reused PID."""
    if not is_alive(pid, identity):
        return False
    try:
        os.killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def group_members(pgid: int) -> list:
    """The PIDs of the live processes in process group `pgid` (not zombies)."""
    members = []
    if os.path.exists("/proc/self/stat"):
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % name, encoding="utf-8", errors="replace") as handle:
                    fields = handle.read().rsplit(")", 1)[1].split()
                if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                    members.append(int(name))
            except (OSError, IndexError, ValueError):
                pass
        return members
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=", "-o", "pgid=", "-o", "stat="], capture_output=True, text=True, timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1] == str(pgid) and fields[0].isdigit() and not fields[2].startswith("Z"):
            members.append(int(fields[0]))
    return members


def owned_member(pid: int, scratch: str, log: str, marker) -> bool:
    """True if process `pid` shows that it is a copy's: it works in the
    copy's scratch, writes its stdout or stderr to the copy's log, or has
    the copy's SECLAB_RUN in its environment. Each survives a change of
    the others (a chdir, a redirection, a new environment); macOS hides
    the environment of its own programs (sleep, sh), not their files."""

    def inside(path):
        return path == scratch or path.startswith(scratch + os.sep)

    wanted = ("%s=%s" % (RUN_MARK, marker)) if marker else None
    if os.path.exists("/proc/self/stat"):
        for link in ("cwd", "fd/1", "fd/2"):
            try:
                path = os.readlink("/proc/%d/%s" % (pid, link))
            except OSError:
                continue
            if inside(path) if link == "cwd" else path == log:
                return True
        if not wanted:
            return False
        try:
            with open("/proc/%d/environ" % pid, "rb") as handle:
                return wanted.encode() in handle.read().split(b"\0")
        except OSError:
            return False
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd,1,2", "-Fn"], capture_output=True, text=True, timeout=10, check=False
        )
        fd = None
        for line in result.stdout.splitlines():
            if line.startswith("f"):
                fd = line[1:]
            elif line.startswith("n") and (inside(line[1:]) if fd == "cwd" else line[1:] == log):
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    if not wanted:
        return False
    try:
        result = subprocess.run(
            ["ps", "eww", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=dict(os.environ, LC_ALL="C"),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return re.search(r"(^|\s)%s(\s|$)" % re.escape(wanted), result.stdout) is not None


def orphaned_group(copy: dict, rdir: str) -> bool:
    """True if a copy's process group outlived its leader: no process has
    the leader's PID any more, and a process still in the group shows it
    is the copy's (owned_member). A PID isn't reused while a group with
    that id exists, so a group with the copy's marks is the copy's, not a
    recycled one."""
    pid = copy.get("pid")
    if not pid or not copy.get("identity"):
        return False
    try:
        os.kill(pid, 0)
        if process_identity(pid) is not None:
            return False  # the leader, or a process that reused its PID
        # A zombie leader: its PID can't be reused until it's reaped.
    except ProcessLookupError:
        pass
    except PermissionError:
        return False
    try:
        os.killpg(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False  # no group left that could be signalled
    paths = copy_paths(rdir, copy["index"])
    scratch = os.path.realpath(paths["scratch"])
    log = os.path.realpath(paths["log"])
    return any(owned_member(member, scratch, log, copy.get("marker")) for member in group_members(pid))


def signal_copy(copy: dict, rdir: str, sig) -> bool:
    """Signal a copy's process group through its leader, or, when the
    leader is gone, the group it left behind."""
    if signal_group(copy.get("pid"), copy.get("identity"), sig):
        return True
    if not orphaned_group(copy, rdir):
        return False
    try:
        os.killpg(copy["pid"], sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# --------------------------------------------------------------------------
# Workspace and runs


def default_workspace() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(here) == "bin":
        return os.path.dirname(here)
    return os.path.abspath(os.path.join(".seclab", "fuzz"))


def default_tree(workspace: str) -> str:
    parts = os.path.abspath(workspace).split(os.sep)
    if len(parts) >= 2 and parts[-2:] == [".seclab", "fuzz"]:
        return os.sep.join(parts[:-2]) or os.sep
    return os.path.abspath(".")


def run_dir(workspace: str, run_id: str) -> str:
    if not run_id or "/" in run_id or run_id.startswith("."):
        raise LauncherError("invalid run id: %r" % run_id)
    return os.path.join(os.path.abspath(workspace), "runs", run_id)


def substitute(text: str, values: dict) -> str:
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


def copy_paths(rdir: str, index: int) -> dict:
    scratch = os.path.join(rdir, "copy-%d" % index, "work")
    return {
        "run": rdir,
        "scratch": scratch,
        "tmp": os.path.join(scratch, "tmp"),
        "copy": str(index),
        "log": os.path.join(rdir, "copy-%d" % index, "log"),
    }


def expand_findings(patterns: list) -> list:
    """Existing finding directories for a copy (patterns may be globs)."""
    dirs = []
    for pattern in patterns:
        matches = glob.glob(pattern) if glob.has_magic(pattern) else [pattern]
        for match in sorted(matches):
            if os.path.isdir(match):
                dirs.append(match)
    return dirs


def list_findings(patterns: list) -> list:
    files = []
    for directory in expand_findings(patterns):
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            path = os.path.join(directory, name)
            if NOT_FINDINGS.match(name) or not os.path.isfile(path):
                continue
            files.append(path)
    return files


def finding_kind(path: str) -> str:
    name = os.path.basename(path)
    for prefix in ("crash", "timeout", "oom", "leak"):
        if name.startswith(prefix + "-"):
            return prefix
    for suffix in ("timeout", "crash", "oom", "leak"):
        if name.endswith("-" + suffix):
            return suffix
    parent = os.path.basename(os.path.dirname(path))
    if parent == "crashes":
        return "crash"
    if parent == "hangs":
        return "hang"
    return "finding"


def count_files(directory) -> int:
    if not directory or not os.path.isdir(directory):
        return 0
    try:
        return sum(
            1
            for name in os.listdir(directory)
            if not name.startswith(".") and os.path.isfile(os.path.join(directory, name))
        )
    except OSError:
        return 0


def last_status_line(log_path: str) -> str:
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "rb") as handle:
            handle.seek(max(0, size - 65536))
            tail = ANSI_CODE.sub("", handle.read().decode("utf-8", errors="replace"))
    except OSError:
        return ""
    lines = [line.strip() for line in tail.replace("\r", "\n").splitlines() if line.strip()]
    for line in reversed(lines):
        if STATUS_LINE.match(line):
            return line[:300]
    return lines[-1][:300] if lines else ""


def trim_log(path: str) -> bool:
    """Keep a log's head and tail once it grows past LOG_MAX_BYTES. The
    copy writes in append mode, so it keeps appending after the cut."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size <= LOG_MAX_BYTES:
        return False
    try:
        with open(path, "rb") as handle:
            head = handle.read(LOG_HEAD_BYTES)
            handle.seek(max(LOG_HEAD_BYTES, size - LOG_TAIL_BYTES))
            tail = handle.read()
        marker = (
            "\n[launcher: %d bytes cut from the middle of this log at %s]\n"
            % (size - len(head) - len(tail), iso(now()))
        ).encode()
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        try:
            os.ftruncate(fd, 0)
            os.write(fd, head + marker + tail)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def tree_state(path: str) -> dict:
    state = {"path": path, "commit": None, "dirty": None}
    try:
        head = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if head.returncode == 0:
            state["commit"] = head.stdout.strip()
            status = subprocess.run(
                ["git", "-C", path, "status", "--porcelain", "--untracked-files=no"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            state["dirty"] = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return state


# --------------------------------------------------------------------------
# States


def runs_jvm(commands: list) -> bool:
    return any(JVM_COMMAND.search(str(arg)) for command in commands for arg in command)


def stop_grace(commands: list) -> float:
    """TERM-to-KILL grace: longer when a copy runs a JVM."""
    return JVM_GRACE_SECONDS if runs_jvm(commands) else STOP_GRACE_SECONDS


def mark_stop(rdir: str, index: int) -> float:
    """The time of the stop on the file system's clock: file times come from a
    coarser clock than time.time(), so a file written right after the TERM
    could otherwise look older than the stop."""
    marker = os.path.join(rdir, "copy-%d" % index, "stopped")
    try:
        with open(marker, "w"):
            pass
        return os.path.getmtime(marker)
    except OSError:
        return now()


def split_findings(copy: dict, rdir: str) -> list:
    """The copy's new findings. Files written after the launcher's TERM go to
    copy["after_stop"] instead: libFuzzer's exit hook saves the input in
    flight when a stopped JVM exits ("fuzz target exited"), which isn't a bug."""
    baseline = set(copy.get("baseline", []))
    found = [f for f in list_findings(copy.get("findings", [])) if f not in baseline]
    stopped = copy.get("stopped_at")
    late = []
    if stopped is not None:
        late = [f for f in found if os.path.exists(f) and os.path.getmtime(f) >= stopped]
        found = [f for f in found if f not in late]
    copy["after_stop"] = [{"path": os.path.relpath(f, rdir), "kind": finding_kind(f)} for f in late]
    return found


def copy_state(copy: dict, spec: dict, ended_at, findings: int, reason: str, ran=None) -> str:
    """Why a copy ended (D1). `reason` is how the launcher saw it end:
    'exit', 'deadline', 'timeout' or 'stopped'; `ran` is how long it ran
    awake (default: by the wall clock)."""
    if reason == "stopped":
        return "stopped"
    if spec.get("timeout") is not None:
        if reason == "timeout":
            return "timed out"
        code = copy.get("returncode")
        return "finished" if code == 0 else "exited %s" % code
    limit = spec.get("time")
    if reason == "deadline":
        return "finished"
    if limit is not None and copy.get("returncode") == 0:
        wall = ended_at - copy["started"]
        tolerance = max(2.0, min(60.0, 0.05 * limit))
        # The engine's own limit. Engines time it by the wall clock: after a
        # sleep they stop early by the awake clock.
        if max(wall, wall if ran is None else ran) >= limit - tolerance:
            return "finished"
    return "found-crash" if findings else "failed"


def summarize(states: list, findings: int) -> str:
    counts: dict = {}
    for state in states:
        counts[state] = counts.get(state, 0) + 1
    if len(counts) == 1:
        text = next(iter(counts))
    else:
        order = ["running", "running (unsupervised)", "unverified", "finished", "found-crash", "failed", "timed out",
                 "stopped", "died"]
        keys = sorted(counts, key=lambda k: (order.index(k) if k in order else len(order), k))
        text = ", ".join("%d %s" % (counts[k], k) for k in keys)
    return "%s, %d finding%s" % (text, findings, "" if findings == 1 else "s")


def live_view(rdir: str) -> dict:
    """meta.json, with states worked out for runs whose supervisor is gone."""
    meta = read_json(os.path.join(rdir, "meta.json"))
    if meta.get("ended"):
        return meta
    supervisor = liveness(meta.get("supervisor", {}).get("pid"), meta.get("supervisor", {}).get("identity"))
    supervised = supervisor is True
    states = []
    total = 0
    for copy in meta.get("copies", []):
        alive = liveness(copy.get("pid"), copy.get("identity"))
        found = list_findings(copy.get("findings", []))
        new = [f for f in found if f not in set(copy.get("baseline", []))]
        copy["findings_now"] = [{"path": os.path.relpath(f, rdir), "kind": finding_kind(f)} for f in new]
        total += len(new)
        if copy.get("state") and copy.get("ended"):
            states.append(copy["state"])
            continue
        if supervised or alive and supervisor is None:
            copy["state"] = "running"
        elif alive is None or supervisor is None:
            copy["state"] = "unverified"  # alive or not: identities can't be read here
        elif alive or orphaned_group(copy, rdir):
            copy["state"] = "running (unsupervised)"
        else:
            copy["state"] = "died"
        states.append(copy["state"])
    if not meta.get("copies"):
        states = ["running" if supervised else "unverified" if supervisor is None else "died"]
    meta["state"] = summarize(states, total)
    meta["findings_total"] = total
    meta["supervised"] = supervisor  # None: can't be told here
    if supervisor is False and not any(s.startswith(("running", "unverified")) for s in states):
        meta["last_seen"] = last_seen(rdir, meta)  # died: when it was last heard of
    return meta


def last_seen(rdir: str, meta: dict) -> float:
    """The last write to a run's records or its copies' logs."""
    paths = [os.path.join(rdir, name) for name in ("meta.json", "progress.tsv")]
    paths += [copy["log"] for copy in meta.get("copies", []) if copy.get("log")]
    times = []
    for path in paths:
        try:
            times.append(os.path.getmtime(path))
        except OSError:
            pass
    return max(times) if times else now()


def elapsed(meta: dict) -> float:
    """From the start to the end (a run that died: to when it was last heard of), or to now."""
    if not meta.get("started"):
        return 0.0
    return max(0.0, (meta.get("ended") or meta.get("last_seen") or now()) - meta["started"])


def is_running(meta: dict) -> bool:
    """Not ended, as far as can be told: unverified copies count as running."""
    return not meta.get("ended") and any(
        c.get("state", "").startswith(("running", "unverified")) for c in meta.get("copies", [])
    )


def unverified(meta: dict) -> int:
    return sum(1 for c in meta.get("copies", []) if c.get("state") == "unverified" and not c.get("ended"))


UNVERIFIED_TEXT = (
    "can't verify the processes of %s: process identities can't be read here (a sandbox?), so it isn't taken "
    "for ended. Run status where they can be read, e.g. outside the sandbox"
)


# --------------------------------------------------------------------------
# Supervisor


class Supervisor:
    def __init__(self, workspace: str, run_id: str, foreground: bool) -> None:
        self.workspace = workspace
        self.run_id = run_id
        self.rdir = run_dir(workspace, run_id)
        self.foreground = foreground
        self.spec = read_json(os.path.join(self.rdir, "spec.json"))
        self.meta = read_json(os.path.join(self.rdir, "meta.json"))
        self.stop_requested = False
        self.procs: dict = {}
        self.reasons: dict = {}
        self.kill_at: dict = {}
        self.awake_at: dict = {}
        self.write_error = None
        self.nonce = os.urandom(8).hex()

    # Signals --------------------------------------------------------------

    def install_signals(self) -> None:
        def handler(_signum, _frame):
            self.stop_requested = True

        self.old_handlers = {}
        wanted = {signal.SIGTERM: handler}
        for sig in (signal.SIGINT, signal.SIGHUP):
            # In the foreground, keep what the caller ignores (`nohup`, a background job).
            ignored = signal.getsignal(sig) == signal.SIG_IGN
            wanted[sig] = handler if self.foreground and not ignored else signal.SIG_IGN
        for sig, action in wanted.items():
            self.old_handlers[sig] = signal.signal(sig, action)

    def restore_signals(self) -> None:
        for sig, action in getattr(self, "old_handlers", {}).items():
            signal.signal(sig, action)

    # Copies ---------------------------------------------------------------

    def spawn(self, index: int, entry: dict) -> None:
        values = copy_paths(self.rdir, index)
        for key in ("scratch", "tmp"):
            os.makedirs(values[key], exist_ok=True)
        os.makedirs(os.path.join(values["scratch"], "home"), exist_ok=True)
        os.makedirs(os.path.join(values["scratch"], "out"), exist_ok=True)
        findings = [substitute(p, values) for p in entry.get("findings", [])]
        for pattern in findings:
            if not glob.has_magic(pattern):
                os.makedirs(pattern, exist_ok=True)
        corpus = substitute(entry.get("corpus") or "", values)
        if corpus:
            os.makedirs(corpus, exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG") or "C.UTF-8",
        }
        merged = dict(self.spec.get("env") or {})
        merged.update(entry.get("env") or {})
        for key, value in merged.items():
            env[key] = substitute(str(value), values)
        env["HOME"] = os.path.join(values["scratch"], "home")
        env["TMPDIR"] = values["tmp"]
        java = '-Duser.home="%s" -Djava.io.tmpdir="%s"' % (env["HOME"], values["tmp"])
        env["JAVA_TOOL_OPTIONS"] = (env["JAVA_TOOL_OPTIONS"] + " " + java) if env.get("JAVA_TOOL_OPTIONS") else java
        marker = "%s/%d/%s" % (self.run_id, index, self.nonce)
        env[RUN_MARK] = marker
        argv = [substitute(str(a), values) for a in entry["command"]]
        baseline = list_findings(findings)
        log_fd = os.open(values["log"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        started = now()
        self.awake_at[index] = awake()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=values["scratch"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as error:
            os.write(log_fd, ("launcher: cannot start %s: %s\n" % (argv[0], error)).encode())
            os.close(log_fd)
            self.meta["copies"].append(
                {
                    "index": index,
                    "pid": None,
                    "identity": None,
                    "started": started,
                    "ended": started,
                    "returncode": EXIT_CANT_START,
                    "state": "failed to start: %s" % error,
                    "findings": findings,
                    "baseline": baseline,
                    "corpus": corpus,
                    "log": values["log"],
                    "new_findings": [],
                }
            )
            raise CantStart(error) from None
        os.close(log_fd)
        self.procs[index] = proc
        self.meta["copies"].append(
            {
                "index": index,
                "pid": proc.pid,
                "identity": process_identity(proc.pid),
                "marker": marker,
                "started": started,
                "ended": None,
                "returncode": None,
                "state": "running",
                "findings": findings,
                "baseline": baseline,
                "corpus": corpus,
                "log": values["log"],
                "new_findings": [],
            }
        )

    def copy_by_index(self, index: int) -> dict:
        for copy in self.meta["copies"]:
            if copy["index"] == index:
                return copy
        raise KeyError(index)

    def new_findings(self, copy: dict) -> list:
        return split_findings(copy, self.rdir)

    def request_stop(self, index: int, reason: str) -> None:
        proc = self.procs.get(index)
        if proc is None or proc.poll() is not None or index in self.kill_at:
            return
        self.reasons[index] = reason
        self.copy_by_index(index)["stopped_at"] = mark_stop(self.rdir, index)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        self.kill_at[index] = awake() + stop_grace([c["command"] for c in self.spec["copies"]])

    def reap(self) -> None:
        for index, proc in list(self.procs.items()):
            code = proc.poll()
            if code is None:
                if index in self.kill_at and awake() >= self.kill_at[index]:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                continue
            # Clean up anything the copy left running in its group.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            del self.procs[index]
            copy = self.copy_by_index(index)
            ended = now()
            copy["ended"] = ended
            copy["returncode"] = code if code >= 0 else 128 - code
            found = self.new_findings(copy)
            copy["new_findings"] = [
                {"path": os.path.relpath(f, self.rdir), "kind": finding_kind(f)} for f in found
            ]
            ran = awake() - self.awake_at[index]
            copy["state"] = copy_state(copy, self.spec, ended, len(found), self.reasons.get(index, "exit"), ran)
            self.save()

    def abort(self) -> None:
        """Stop the copies that are still running when the supervisor itself
        fails, so they never run on unsupervised."""
        for proc in self.procs.values():
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        limit = awake() + STOP_GRACE_SECONDS
        while awake() < limit and any(proc.poll() is None for proc in self.procs.values()):
            time.sleep(POLL_SECONDS)
        for proc in self.procs.values():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
        self.procs.clear()

    # Records --------------------------------------------------------------

    def write_failed(self, error: OSError) -> None:
        """A full disk or similar: say so once and keep supervising."""
        if self.write_error is None:
            self.write_error = error
            say(
                "warning: can't write the run's records (%s); the run goes on, but meta.json and "
                "progress.tsv may be out of date" % error,
                sys.stderr,
            )

    def save(self, strict: bool = False) -> None:
        """Write meta.json. While starting (`strict`), a write error ends the
        start: a copy the record doesn't name can't be stopped later."""
        total = 0
        states = []
        for copy in self.meta["copies"]:
            if copy.get("ended"):
                total += len(copy.get("new_findings", []))
            else:
                found = self.new_findings(copy)
                copy["new_findings"] = [
                    {"path": os.path.relpath(f, self.rdir), "kind": finding_kind(f)} for f in found
                ]
                total += len(found)
            states.append(copy.get("state", "running"))
        self.meta["state"] = summarize(states, total)
        self.meta["findings_total"] = total
        try:
            write_json(os.path.join(self.rdir, "meta.json"), self.meta)
        except OSError as error:
            if strict:
                raise
            self.write_failed(error)

    def failed_start(self, started: float, error: Exception) -> None:
        """Roll back a start that failed part-way: stop the copies already
        started, and record why."""
        self.abort()
        ended = now()
        for copy in self.meta["copies"]:
            if not copy.get("ended"):
                copy["ended"] = ended
                copy["state"] = "stopped"
        self.meta["started"] = started
        self.meta["ended"] = ended
        self.meta["error"] = str(error)
        self.meta["state"] = "failed to start: %s" % error
        try:
            write_json(os.path.join(self.rdir, "meta.json"), self.meta)
        except OSError:
            pass

    def sample(self) -> None:
        path = os.path.join(self.rdir, "progress.tsv")
        new = not os.path.exists(path)
        rows = []
        if new:
            rows.append("time\telapsed_s\tcopy\tstate\tcorpus_files\tfindings\tstatus_line\n")
        for copy in self.meta["copies"]:
            end = copy.get("ended") or now()
            status = last_status_line(copy["log"]).replace("\t", " ")
            rows.append(
                "%s\t%d\t%d\t%s\t%d\t%d\t%s\n"
                % (
                    iso(now()),
                    int(end - copy["started"]),
                    copy["index"],
                    copy.get("state", ""),
                    count_files(copy.get("corpus")),
                    len(copy.get("new_findings", [])),
                    status,
                )
            )
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("".join(rows))
        except OSError as error:
            self.write_failed(error)

    def progress_line(self) -> str:
        corpus = sum(count_files(c.get("corpus")) for c in self.meta["copies"])
        corpora = {c.get("corpus") for c in self.meta["copies"] if c.get("corpus")}
        if len(corpora) == 1:
            corpus = count_files(next(iter(corpora)))
        return "[%s] %s: %s; corpus %d files" % (duration(elapsed(self.meta)), self.run_id, self.meta["state"], corpus)

    # Main loop ------------------------------------------------------------

    def run(self) -> int:
        self.install_signals()
        try:
            return self.supervise()
        finally:
            self.abort()
            self.restore_signals()

    def supervise(self) -> int:
        self.meta["supervisor"] = {"pid": os.getpid(), "identity": process_identity(os.getpid())}
        self.meta["copies"] = []
        started = now()
        clock = awake()
        # Each copy is on record as soon as it runs; "started" marks the end
        # of the start.
        try:
            self.save(strict=True)
            for index, entry in enumerate(self.spec["copies"]):
                self.spawn(index, entry)
                self.save(strict=True)
            self.meta["started"] = started
            self.save(strict=True)
        except CantStart as error:
            self.failed_start(started, error)
            return EXIT_CANT_START
        except Exception as error:
            self.failed_start(started, error)
            raise
        self.sample()
        limit = self.spec.get("time")
        timeout = self.spec.get("timeout")
        deadline = None
        if limit is not None:
            jvm = runs_jvm([c["command"] for c in self.spec["copies"]])
            grace = JVM_GRACE_SECONDS if jvm else DEADLINE_GRACE_SECONDS
            deadline = clock + limit + max(grace, min(60.0, 0.05 * limit))
        elif timeout is not None:
            deadline = clock + timeout
        stop_marker = os.path.join(self.rdir, "stop")
        next_progress = clock + PROGRESS_SECONDS
        next_log_check = clock + LOG_CHECK_SECONDS
        while self.procs:
            time.sleep(POLL_SECONDS)
            if self.stop_requested or os.path.exists(stop_marker):
                for index in list(self.procs):
                    self.request_stop(index, "stopped")
            elif deadline is not None and awake() >= deadline:
                for index in list(self.procs):
                    self.request_stop(index, "timeout" if timeout is not None else "deadline")
            self.reap()
            if awake() >= next_log_check:
                for copy in self.meta["copies"]:
                    trim_log(copy["log"])
                next_log_check = awake() + LOG_CHECK_SECONDS
            if awake() >= next_progress:
                self.save()
                self.sample()
                if self.foreground:
                    say(self.progress_line())
                next_progress = awake() + PROGRESS_SECONDS
        for copy in self.meta["copies"]:
            trim_log(copy["log"])  # a run shorter than LOG_CHECK_SECONDS too
        self.meta["ended"] = now()
        self.save()
        self.sample()
        if not LONG_RUN_ID.search(self.run_id):
            for copy in self.meta["copies"]:
                shutil.rmtree(copy_paths(self.rdir, copy["index"])["scratch"], ignore_errors=True)
        return self.result_code()

    def result_code(self) -> int:
        copies = self.meta["copies"]
        if any(c["state"].startswith("failed to start") for c in copies):
            return EXIT_CANT_START
        if self.spec.get("timeout") is not None and len(copies) == 1:
            copy = copies[0]
            if copy["state"] == "timed out":
                return EXIT_TIMED_OUT
            return copy.get("returncode") or 0
        return 0


# --------------------------------------------------------------------------
# Commands


def check_seconds(name: str, value) -> None:
    """A time limit is a finite number of seconds above 0 (engines take 0
    for no limit; here that's leaving --time out)."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        hint = {"--time": "; for no time limit, omit --time", "spec.json's time": "; for no time limit, set it to null"}
        raise LauncherError("%s must be more than 0 seconds, got %r%s" % (name, value, hint.get(name, "")))


def build_spec(args, workspace: str) -> dict:
    if args.time is not None and args.timeout is not None:
        raise LauncherError("--time and --timeout exclude each other")
    check_seconds("--time", args.time)
    check_seconds("--timeout", args.timeout)
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise LauncherError("no command given (start ... -- <command>)")
    if not args.harness:
        raise LauncherError("--harness is required")
    env = {}
    for item in args.env or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise LauncherError("--env takes K=V, got %r" % item)
        env[key] = value
    check_env(env, "--env")
    copies = 1 if args.copies is None else args.copies
    if copies < 1:
        raise LauncherError("--copies must be at least 1")
    for path in args.findings or []:
        check_location(path, "--findings")
    if args.corpus:
        check_location(args.corpus, "--corpus")
    entry = {
        "command": command,
        "env": env,
        "findings": args.findings or ["{run}/artifacts/copy-{copy}"],
        "corpus": args.corpus if args.corpus is not None else "{run}/corpus",
    }
    trees = [os.path.abspath(t) for t in (args.tree or [])] or [default_tree(workspace)]
    return {
        "id": args.id,
        "harness": args.harness,
        "trees": trees,
        "time": args.time,
        "timeout": args.timeout,
        "copies": [dict(entry) for _ in range(copies)],
    }


def check_env(env, where: str) -> None:
    """Values are strings; an empty one is a shell variable the command didn't set (an agent's commands don't keep them)."""
    if env is None:
        return
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise LauncherError("%s must be an object of strings, e.g. {\"AFL_NO_UI\": \"1\"}" % where)
    for key, value in env.items():
        if value == "":
            raise LauncherError(
                "%s %s is empty: the shell variable it came from isn't set in this command. Start every "
                "command with `. .seclab/fuzz/env.sh;` (an agent's commands don't keep shell variables)" % (where, key)
            )


def check_location(path: str, where: str) -> None:
    """A findings or corpus directory: the launcher creates and reads it
    from its own working directory, while the copy runs in scratch."""
    if not os.path.isabs(substitute(path, copy_paths("/run", 0))):
        raise LauncherError(
            "%s %r isn't an absolute path: the launcher's working directory isn't the copy's. Use an absolute "
            "path, or one under {run} or {scratch}" % (where, path)
        )


def strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def check_spec(spec) -> None:
    """spec.json's keys and their types: AFL++ specs are written by hand."""
    if not isinstance(spec, dict):
        raise LauncherError("spec.json must be an object")
    for key in ("id", "harness"):
        if not spec.get(key) or not isinstance(spec[key], str):
            raise LauncherError("spec.json needs %r, a string" % key)
    copies = spec.get("copies")
    if not copies or not isinstance(copies, list) or not all(isinstance(entry, dict) for entry in copies):
        raise LauncherError("spec.json needs 'copies', a list with an object per copy")
    if spec.get("trees") is not None and not strings(spec["trees"]):
        raise LauncherError("spec.json's trees must be a list of paths")
    check_env(spec.get("env"), "spec.json's env")
    if spec.get("time") is not None and spec.get("timeout") is not None:
        raise LauncherError("spec.json sets both time and timeout")
    check_seconds("spec.json's time", spec.get("time"))
    check_seconds("spec.json's timeout", spec.get("timeout"))
    for index, entry in enumerate(copies):
        where = "spec.json's copy %d" % index
        if not entry.get("command") or not strings(entry["command"]):
            raise LauncherError("%s needs a command, a list of strings (the argv)" % where)
        entry.setdefault("env", {})
        check_env(entry["env"], where + " env")
        findings = entry.setdefault("findings", ["{run}/artifacts/copy-{copy}"])
        if not strings(findings):
            raise LauncherError("%s findings must be a list of directories" % where)
        corpus = entry.setdefault("corpus", "{run}/corpus")
        if corpus is not None and not isinstance(corpus, str):
            raise LauncherError("%s corpus must be one directory (a string)" % where)
        for path in findings:
            check_location(path, where + " findings")
        if corpus:
            check_location(corpus, where + " corpus")
    if spec.get("trees") is None:
        spec["trees"] = []


# libFuzzer (and Jazzer, Jazzer.js) runs these modes by re-running its own
# command line through the shell without quoting: a path with a space or a
# shell character splits (every inner run fails, and the result is empty)
# or runs what follows it. Non-ASCII letters are fine.
RERUN_FLAGS = ("-merge=", "-fork=", "-jobs=", "-minimize_crash=", "-set_cover_merge=")
SHELL_UNSAFE = re.compile(r"[\s'\"\\$`!*?;&|<>(){}\[\]#~]")  # as in init.py
# In other arguments, what splits or runs commands (a glob such as Jazzer's
# --instrumentation_includes=com.example.** matches nothing in scratch).
SHELL_SPLIT = re.compile(r"^[#~]|[\s'\"\\$`;&|<>()]")
FLAG_PREFIX = re.compile(r"^--?[A-Za-z0-9_.-]+=")
SHELLS = ("sh", "bash", "dash", "zsh", "ksh")


def shell_script(command: list):
    """The index of a shell's -c script in `command` (`sh -c '<script>'
    ...`), or None: its words are the shell's to split."""
    if command and os.path.basename(str(command[0])) in SHELLS:
        for index, arg in enumerate(command[1:-1], 1):
            if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", str(arg)):
                return index + 1
            if not str(arg).startswith("-"):
                break
    return None


def check_rerun_paths(spec: dict, rdir: str) -> None:
    """Refuse a re-running command when the run directory, or one of its
    arguments (e.g. a finding named after an AFL++ seed, a relative path
    with a space), has a shell character."""
    for index, entry in enumerate(spec["copies"]):
        if not any(flag in str(arg) for arg in entry["command"] for flag in RERUN_FLAGS):
            continue
        bad = SHELL_UNSAFE.search(rdir)
        if bad:
            raise LauncherError(
                "refused: %s uses libFuzzer's -merge, -fork, -jobs or -minimize_crash, which re-run the "
                "command through the shell without quoting, but the run directory %s contains %r: every "
                "inner run would fail and the result would be silently empty. Use a checkout path without "
                "spaces or shell characters." % (spec["id"], rdir, bad.group(0))
            )
        values = copy_paths(rdir, index)
        script = shell_script(entry["command"])
        for position, arg in enumerate(entry["command"]):
            if position == script:
                continue  # the shell's to split
            path = FLAG_PREFIX.sub("", substitute(str(arg), values), count=1)
            bad = SHELL_UNSAFE.search(path) if path.startswith("/") else SHELL_SPLIT.search(path)
            if bad:
                raise LauncherError(
                    "refused: %s uses libFuzzer's -merge, -fork, -jobs or -minimize_crash, which re-run the "
                    "command through the shell without quoting, but the argument %s contains %r: the inner runs "
                    "would split it, or run what follows it. Use paths without spaces or shell characters; copy "
                    "a file to such a name first (a finding: to crashes/<bucket>/input)." % (spec["id"], path, bad.group(0))
                )


def claim_run(rdir: str, meta: dict) -> None:
    """Create a run's meta.json, exclusively: of two starts with one id,
    only one gets the run."""
    os.makedirs(rdir, exist_ok=True)
    path = os.path.join(rdir, "meta.json")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise LauncherError("run %s has already started (%s); pick a new id" % (meta["id"], rdir)) from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError:
        release_run(rdir)
        raise


def release_run(rdir: str) -> None:
    """Undo claim_run for a start that ran nothing (the id stays free)."""
    try:
        os.remove(os.path.join(rdir, "meta.json"))
        os.rmdir(rdir)  # only if empty
    except OSError:
        pass


def running_cores(workspace: str, skip: str) -> tuple:
    root = os.path.join(workspace, "runs")
    cores, runs = 0, []
    if not os.path.isdir(root):
        return 0, []
    for name in sorted(os.listdir(root)):
        if name == skip or not os.path.exists(os.path.join(root, name, "meta.json")):
            continue
        try:
            meta = live_view(os.path.join(root, name))
        except (OSError, ValueError):
            continue
        alive = sum(1 for c in meta.get("copies", []) if c.get("state", "").startswith(("running", "unverified")))
        if not meta.get("started") and not meta.get("ended") and meta.get("supervised") is not False:
            alive = max(alive, meta.get("reserved") or 0)  # a start in progress
        if alive:
            cores += alive
            runs.append("%s (%d)" % (name, alive))
    return cores, runs


def refuse_live(run_id: str, rdir: str, workspace: str) -> None:
    """Refuse a run id that is still running, with a message that says so
    (an ended run's id is refused afterwards: ids are never reused)."""
    if not os.path.exists(os.path.join(rdir, "meta.json")):
        return
    try:
        meta = live_view(rdir)
    except (OSError, ValueError):
        return
    if not meta.get("ended") and (meta.get("supervised") is not False or is_running(meta)):
        raise LauncherError("run %s is still running (%s); pick a new id" % (run_id, meta.get("state", "")))


def cmd_start(args) -> int:
    workspace = os.path.abspath(args.workspace or default_workspace())
    if args.spec:
        spec = read_json(args.spec)
        if args.id and isinstance(spec, dict):
            spec["id"] = args.id
        check_spec(spec)
    else:
        if not args.id:
            raise LauncherError("--id is required")
        spec = build_spec(args, workspace)
    if not spec["trees"]:
        spec["trees"] = [default_tree(workspace)]
    rdir = run_dir(workspace, spec["id"])
    check_rerun_paths(spec, rdir)
    refuse_live(spec["id"], rdir, workspace)
    if args.detach and process_identity(os.getpid()) in (None, UNKNOWN):
        raise LauncherError(
            "process identities can't be read here (a sandbox?): status, wait and stop couldn't tell a detached "
            "run from a reused PID. Run it in the foreground, without --detach"
        )
    if getattr(args, "prepare", False):
        if os.path.exists(os.path.join(rdir, "meta.json")):
            raise LauncherError("run %s has already started (%s); pick a new id" % (spec["id"], rdir))
        os.makedirs(rdir, exist_ok=True)
        write_json(os.path.join(rdir, "spec.json"), spec)
        say("prepared %s; start it with:" % spec["id"])
        say("  " + shlex.join(["python3", os.path.abspath(__file__), "--workspace", workspace, "start", "--spec", os.path.join(rdir, "spec.json")]))
        return 0
    if os.path.exists(os.path.join(rdir, "meta.json")):
        raise LauncherError("run %s has already started (%s); pick a new id" % (spec["id"], rdir))
    wanted = len(spec["copies"])
    meta = {
        "id": spec["id"],
        "harness": spec["harness"],
        "kind": "self-ending" if spec.get("timeout") is not None else "timed",
        "time": spec.get("time"),
        "timeout": spec.get("timeout"),
        "trees": [],
        "created": now(),
        "started": None,
        "ended": None,
        "copies": [],
        "state": "starting",
        # This process holds the run until the supervisor takes over; the
        # CPU check counts the reserved copies of a start in progress.
        "supervisor": {"pid": os.getpid(), "identity": process_identity(os.getpid())},
        "reserved": wanted,
    }
    claim_run(rdir, meta)
    try:
        if not args.skip_cpu_check:
            busy, runs = running_cores(workspace, spec["id"])
            if busy + wanted > cpu_count():
                release_run(rdir)
                argv = list(getattr(args, "argv", None) or sys.argv[1:])
                if "start" in argv:
                    argv.insert(argv.index("start") + 1, "--skip-cpu-check")
                argv = ["python3", os.path.abspath(__file__)] + argv
                say(
                    "refused: %s needs %d core%s, but %d of %d are in use by running runs (%s). "
                    "Overloading the machine slows every run, develop runs included."
                    % (spec["id"], wanted, "" if wanted == 1 else "s", busy, cpu_count(), ", ".join(runs)),
                    sys.stderr,
                )
                say("To start it anyway, with the user's OK:\n  " + shlex.join(argv), sys.stderr)
                return EXIT_CPU
        write_json(os.path.join(rdir, "spec.json"), spec)
        meta["trees"] = [tree_state(t) for t in spec["trees"]]
        write_json(os.path.join(rdir, "meta.json"), meta)
        if args.detach:
            log = open(os.path.join(rdir, "supervisor.log"), "ab")
            try:
                subprocess.Popen(
                    [sys.executable, os.path.abspath(__file__), "_supervise", "--workspace", workspace, spec["id"]],
                    env=scrubbed_environ(),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            finally:
                log.close()
    except BaseException:
        release_run(rdir)  # nothing has run yet
        raise
    if args.detach:
        limit = awake() + DETACH_WAIT_SECONDS
        while awake() < limit:
            current = read_json(os.path.join(rdir, "meta.json"))
            if current.get("error"):
                if any(c.get("returncode") == EXIT_CANT_START for c in current.get("copies", [])):
                    say("error: %s %s" % (spec["id"], current["state"]), sys.stderr)
                    return EXIT_CANT_START
                say("error: %s %s; see %s" % (spec["id"], current["state"], os.path.join(rdir, "supervisor.log")), sys.stderr)
                return 1
            if current.get("started"):
                say("started %s (detached): %d cop%s in %s" % (spec["id"], len(spec["copies"]), "y" if len(spec["copies"]) == 1 else "ies", rdir))
                say("check it with: %s" % shlex.join(["python3", os.path.abspath(__file__), "--workspace", workspace, "status", spec["id"]]))
                return 0
            time.sleep(0.1)
        say("error: the supervisor didn't start; see %s" % os.path.join(rdir, "supervisor.log"), sys.stderr)
        return 1
    say("started %s: %d cop%s in %s" % (spec["id"], len(spec["copies"]), "y" if len(spec["copies"]) == 1 else "ies", rdir))
    supervisor = Supervisor(workspace, spec["id"], foreground=True)
    code = supervisor.run()
    print_result(rdir, supervisor.meta)
    return code


def cmd_supervise(args) -> int:
    workspace = os.path.abspath(args.workspace or default_workspace())
    return Supervisor(workspace, args.id, foreground=False).run()


def print_result(rdir: str, meta=None) -> None:
    """Print a run's result line and copies, from `meta` when the caller
    has the latest record in memory (meta.json may be unwritable)."""
    meta = meta if meta is not None else live_view(rdir)
    say("%s: %s (%s)" % (meta["id"], meta["state"], duration(elapsed(meta))))
    for copy in meta.get("copies", []):
        found = copy.get("new_findings") if meta.get("ended") else copy.get("findings_now", [])
        line = "  copy %d: %s, log %s" % (copy["index"], copy.get("state", "?"), copy["log"])
        say(line)
        for item in (found or [])[:10]:
            say("    %s: %s" % (item["kind"], os.path.join(rdir, item["path"])))
        if found and len(found) > 10:
            say("    ... %d more" % (len(found) - 10))
        late = copy.get("after_stop") or []
        if late:
            say("    %d file%s written after the stop, not findings: %s" % (
                len(late), "" if len(late) == 1 else "s", ", ".join(os.path.join(rdir, i["path"]) for i in late[:3])))


def cmd_wait(args) -> int:
    workspace = os.path.abspath(args.workspace or default_workspace())
    rdir = run_dir(workspace, args.id)
    if not os.path.exists(os.path.join(rdir, "meta.json")):
        raise LauncherError("no run %s in %s" % (args.id, workspace))
    limit = awake() + (args.max if args.max is not None else float("inf"))
    while True:
        meta = live_view(rdir)
        if unverified(meta):
            say(UNVERIFIED_TEXT % args.id, sys.stderr)
            return 1
        # A start cut short (its supervisor gone) never gets "started".
        if not is_running(meta) and (meta.get("started") or meta.get("supervised") is False):
            print_result(rdir)
            return 0
        if awake() >= limit:
            alive = sum(1 for c in meta["copies"] if c.get("state", "").startswith("running"))
            text = "%s: still running, %d cop%s" % (args.id, alive, "y" if alive == 1 else "ies")
            spec = read_json(os.path.join(rdir, "spec.json"))
            end = spec.get("time") if spec.get("time") is not None else spec.get("timeout")
            if end is not None and meta.get("started"):
                text += ", %s left" % duration(max(0, meta["started"] + end - now()))
            say(text)
            return 0
        time.sleep(min(POLL_SECONDS * 2, max(0.1, limit - awake())))


def cmd_status(args) -> int:
    workspace = os.path.abspath(args.workspace or default_workspace())
    if args.id:
        rdir = run_dir(workspace, args.id)
        if not os.path.exists(os.path.join(rdir, "meta.json")):
            raise LauncherError("no run %s in %s" % (args.id, workspace))
        meta = live_view(rdir)
        spec = read_json(os.path.join(rdir, "spec.json"))
        say("## %s (harness %s)" % (meta["id"], meta["harness"]))
        say()
        say("- **State:** %s" % meta["state"])
        limit = "time %s" % duration(spec["time"]) if spec.get("time") is not None else (
            "timeout %s" % duration(spec["timeout"]) if spec.get("timeout") is not None else "no time limit"
        )
        say("- **Started:** %s; %s; elapsed %s%s" % (iso(meta.get("started")), limit, duration(elapsed(meta)),
                                                    " (to its last output)" if meta.get("last_seen") else ""))
        for tree in meta.get("trees", []):
            say("- **Tree:** %s at %s%s" % (tree["path"], (tree.get("commit") or "?")[:12], " (uncommitted changes)" if tree.get("dirty") else ""))
        say("- **Directory:** %s (%s)" % (rdir, human_size(dir_size(rdir))))
        say()
        say("| Copy | PID | State | Findings | Corpus files | Last status line |")
        say("|---|---|---|---|---|---|")
        for copy in meta.get("copies", []):
            found = copy.get("new_findings") if meta.get("ended") else copy.get("findings_now", [])
            say(
                "| %d | %s | %s | %d | %d | %s |"
                % (
                    copy["index"],
                    copy.get("pid") or "-",
                    copy.get("state", "?"),
                    len(found or []),
                    count_files(copy.get("corpus")),
                    last_status_line(copy["log"]).replace("|", "\\|") or "-",
                )
            )
        return 0
    root = os.path.join(workspace, "runs")
    rows = []
    searches: dict = {}
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            rdir = os.path.join(root, name)
            if not os.path.exists(os.path.join(rdir, "meta.json")):
                continue
            try:
                meta = live_view(rdir)
            except (OSError, ValueError):
                continue
            if not (args.all or is_running(meta) or LONG_RUN_ID.search(name)):
                continue
            cores = sum(1 for c in meta.get("copies", []) if c.get("state", "").startswith(("running", "unverified")))
            row = [name, meta.get("harness", ""), meta["state"], elapsed(meta), cores, dir_size(rdir)]
            # reach.py runs one launcher run per step (reach-<h>-<n>-<step>): one row per search,
            # with its last step's state.
            step = re.match(r"^(reach-%s-\d+)-(\d+)$" % re.escape(row[1]), name)
            if not step:
                rows.append(row)
                continue
            search = searches.get(step.group(1))
            if search is None:
                search = searches[step.group(1)] = {"row": [step.group(1), row[1], "", 0, 0, 0], "steps": 0, "last": -1}
                rows.append(search["row"])
            search["steps"] += 1
            for i in (3, 4, 5):
                search["row"][i] += row[i]
            if int(step.group(2)) > search["last"]:
                search["last"], search["row"][2] = int(step.group(2)), row[2]
        for search in searches.values():
            search["row"][0] += " (%d steps)" % search["steps"]
    if not rows:
        say("no runs" if args.all else "no running runs and no long runs (--all lists every run)")
        return 0
    say("| Run | Harness | State | Elapsed | Cores in use | Size |")
    say("|---|---|---|---|---|---|")
    for name, harness, state, seconds, cores, size in rows:
        say("| %s | %s | %s | %s | %d | %s |" % (name, harness, state, duration(seconds), cores, human_size(size)))
    return 0


def cmd_stop(args) -> int:
    workspace = os.path.abspath(args.workspace or default_workspace())
    rdir = run_dir(workspace, args.id)
    if not os.path.exists(os.path.join(rdir, "meta.json")):
        raise LauncherError("no run %s in %s" % (args.id, workspace))
    meta = live_view(rdir)
    if meta.get("ended") or meta.get("last_seen"):
        say("%s already ended: %s" % (args.id, meta["state"]))
        if not meta.get("ended"):
            warn_cut_short(meta)
        return 0
    try:
        with open(os.path.join(rdir, "stop"), "w", encoding="utf-8") as handle:
            handle.write(iso(now()) + "\n")
    except OSError:
        pass  # the supervisor also gets a TERM
    supervisor = meta.get("supervisor", {})
    if liveness(supervisor.get("pid"), supervisor.get("identity")) is None or any(
        liveness(c.get("pid"), c.get("identity")) is None for c in meta.get("copies", []) if not c.get("ended")
    ):
        # Signalling could hit a reused PID, and the copies' end can't be checked.
        say("error: %s; the stop request is written, which a running supervisor follows. "
            "It isn't recorded as stopped." % (UNVERIFIED_TEXT % args.id), sys.stderr)
        return 1

    def supervised() -> bool:
        return is_alive(supervisor.get("pid"), supervisor.get("identity"))

    if signal_group_or_pid(supervisor.get("pid"), supervisor.get("identity")):
        limit = awake() + 2 * STOP_GRACE_SECONDS
        while awake() < limit and not live_view(rdir).get("ended") and supervised():
            time.sleep(0.2)
        if live_view(rdir).get("ended"):
            print_result(rdir)
            return 0
        if supervised():
            print_result(rdir)
            say("error: %s is still running: its supervisor didn't end it; run stop again" % args.id, sys.stderr)
            return 1
    # No supervisor (or it ended without writing its end record): stop the
    # copies directly and write the end record, once they are gone.
    current = read_json(os.path.join(rdir, "meta.json"))
    live = [c for c in current.get("copies", []) if not c.get("ended")]

    def left():
        return [c for c in live if is_alive(c.get("pid"), c.get("identity")) or orphaned_group(c, rdir)]

    try:
        grace = stop_grace([c["command"] for c in read_json(os.path.join(rdir, "spec.json"))["copies"]])
    except (OSError, ValueError, KeyError):
        grace = JVM_GRACE_SECONDS
    for copy in live:
        copy["stopped_at"] = mark_stop(rdir, copy["index"])
        signal_copy(copy, rdir, signal.SIGTERM)
    limit = awake() + grace
    while awake() < limit and left():
        time.sleep(0.2)
    for copy in live:
        signal_copy(copy, rdir, signal.SIGKILL)
    limit = awake() + STOP_GRACE_SECONDS
    while awake() < limit and left():
        time.sleep(0.2)
    remaining = left()
    if remaining:
        say(
            "error: %s is still running: cop%s %s didn't stop (PID %s); it isn't recorded as stopped"
            % (
                args.id,
                "y" if len(remaining) == 1 else "ies",
                ", ".join(str(c["index"]) for c in remaining),
                ", ".join(str(c.get("pid")) for c in remaining),
            ),
            sys.stderr,
        )
        return 1
    for copy in live:
        copy["ended"] = now()
        copy["state"] = "stopped"
        copy["new_findings"] = [
            {"path": os.path.relpath(f, rdir), "kind": finding_kind(f)} for f in split_findings(copy, rdir)
        ]
    total = sum(len(c.get("new_findings", [])) for c in current.get("copies", []))
    current["ended"] = now()
    current["state"] = summarize([c.get("state", "stopped") for c in current.get("copies", [])] or ["stopped"], total)
    current["findings_total"] = total
    try:
        write_json(os.path.join(rdir, "meta.json"), current)
    except OSError as error:
        say("warning: the copies are stopped, but meta.json can't be written: %s" % error, sys.stderr)
    warn_cut_short(current)
    print_result(rdir, current)
    return 0


def warn_cut_short(meta: dict) -> None:
    """A supervisor that died between starting a copy and recording it
    leaves a copy that no record names."""
    if not meta.get("started"):
        say("warning: the start of %s didn't complete: a copy started just before it was cut short may not be "
            "on record. Look for processes with %s=%s/ in their environment" % (meta["id"], RUN_MARK, meta["id"]),
            sys.stderr)


def signal_group_or_pid(pid, identity) -> bool:
    """Ask a supervisor to stop (it's its own process group leader when
    detached, but not in the foreground, so signal the PID)."""
    if not is_alive(pid, identity):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# --------------------------------------------------------------------------
# The launcher's own environment

SCRUBBED_MARK = "SECLAB_LAUNCHER_SCRUBBED"


def scrubbed_environ() -> dict:
    """What a launcher process keeps of the caller's environment. A copy can
    read its parent's environment (/proc/<ppid>/environ, `ps eww`), so the
    process that starts the copies keeps only what they get anyway."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        SCRUBBED_MARK: "1",
    }


def reexec_scrubbed(script: str, argv: list) -> None:
    """Replace this process with `python3 script argv` under
    scrubbed_environ(), unless it already runs that way."""
    if os.environ.get(SCRUBBED_MARK) == "1":
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(sys.executable, [sys.executable, os.path.abspath(script)] + list(argv), scrubbed_environ())


# --------------------------------------------------------------------------
# CLI


START_HELP = """\
Each copy gets an empty environment plus PATH and LANG from the caller and
the --env values; SECLAB_RUN=<id>/<copy>/<random> (stop recognizes the
copy's processes by it, by their working directory in scratch or by their
output going to the log); HOME={scratch}/home, TMPDIR={scratch}/tmp, and
JAVA_TOOL_OPTIONS with -Duser.home and -Djava.io.tmpdir there (appended to a
given value); stdin from /dev/null; its own process group; its log at
copy-<i>/log, checked every 5 s and at the end, and cut to its first 4 MB
and last 16 MB past 64 MB (output written during a cut can be lost). The
launcher creates the run directory, each copy's scratch (copy-<i>/work, with
home/, tmp/ and an empty out/), and the findings and corpus directories
(absolute paths); commands create any other output directory.

Timed runs (--time) fuzz: the deadline is --time plus a short grace.
Self-ending runs (--timeout) replay, merge or reproduce: --timeout is a kill
limit. Both count time awake, not time the machine sleeps (elapsed times
shown are by the wall clock). Foreground runs print a progress line every
5 minutes and the result at the end; INT, TERM and HUP stop the copies
(TERM, then KILL after 10 s; 15 s for a JVM, whose timed runs also get at
least 15 s past --time), unless the caller ignores INT or HUP (nohup).
Stopping, and the end of a copy, signal the copy's process group: a process
that leaves it (setsid, a daemon) isn't stopped.
Run ids are never reused: a retry takes the next number.

exit status:
  a single-copy self-ending run returns its command's status (124 if it
  timed out); other runs return 0 when they end; 127 when a copy's
  command can't be started (--detach too; the copies already started are
  stopped); 1 when a run fails to start otherwise; 2 for
  a usage error (e.g. a --time or --timeout of 0: for no time limit, omit
  --time); 3 when the CPU check refuses (the cores of this workspace's
  running runs and starts in progress plus the new copies may not exceed
  the cores it may use: the machine's, limited by CPU affinity and a
  cgroup quota such as a container's --cpus; it knows nothing about other
  software).

path check:
  commands using libFuzzer's -merge, -fork, -jobs or -minimize_crash are
  refused (exit 2) when the run directory, or one of their arguments (a
  finding, -exact_artifact_path=..., a relative -dict=...; not a shell's
  -c script), contains a space or shell character (a glob character only
  in an absolute path): libFuzzer, Jazzer and Jazzer.js re-run their
  command line through the shell unquoted, so every inner run would fail
  (or run what follows the character), the command would still exit 0,
  and the result would be empty. Copy such a finding to a plain name first.

spec.json (what start records and start --spec runs; placeholders stay
unresolved; one copies entry per copy when the copies differ, e.g. the
AFL++ mix; a run-level "env" applies to every copy, a copy's own overrides):
  {"id": "<h>-long1", "harness": "<h>", "trees": ["/abs/worktree"],
   "time": 86400, "timeout": null,
   "copies": [{"command": ["/abs/afl-fuzz", "-M", "main", "..."],
               "env": {"AFL_NO_UI": "1"},
               "findings": ["{run}/out/main/crashes", "{run}/out/main/hangs"],
               "corpus": "{run}/out/main/queue"}, ...]}

states (a run's state sums up its copies, e.g. "2 running, 1 found-crash,
1 finding"):
  running                   still going
  running (unsupervised)    copies alive, supervisor gone
  finished                  timed: ran until the deadline, or exited 0 at
                            the engine's own limit; self-ending: exited 0
  found-crash               timed: ended early with a new finding
  failed                    timed: ended early without a new finding
  exited <code>             self-ending: exited non-zero (a reproduction
                            that reproduces)
  timed out                 self-ending: killed at the timeout
  stopped                   stop, Ctrl-C
  died                      supervisor and copies gone without an end record
                            (elapsed time: until its last output)
  unverified                process identities can't be read here (a
                            sandbox): not taken for ended; wait and stop
                            say so and exit 1
  failed to start: <error>  the copy's command couldn't be started, or the
                            run couldn't be set up; the copies already
                            started were stopped

findings are files that appeared in a copy's findings locations after it
started (not AFL++'s README.txt or libFuzzer's slow-unit-*; commands with a
per-input timeout above 10 s raise -report_slow_units above it). Their kind
comes from the name (crash-, timeout-, oom-, leak-; Jest's finding-timeout)
or the directory (AFL++ crashes/, hangs/). A file written after the
launcher's TERM isn't one: libFuzzer's exit hook saves the input in flight
when a stopped JVM exits ("fuzz target exited"). It's listed apart, as
after_stop.

files per run: meta.json (each tree's commit and whether it had uncommitted
changes, times, copies with PID, process identity, state and findings);
progress.tsv (per copy at the start, every 5 minutes and at the end:
elapsed seconds, state, corpus files, findings, last status line). Develop
runs delete each copy's scratch at the end; long runs keep it.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="launcher.py",
        description="Run fuzzing commands, replays and reproductions for a campaign.",
    )
    parser.add_argument("--workspace", help="the .seclab/fuzz directory (default: found from this script)")
    sub = parser.add_subparsers(dest="cmd", metavar="command")
    sub.required = True

    def with_workspace(p):
        p.add_argument("--workspace", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    p = sub.add_parser(
        "start", help="start a run", epilog=START_HELP, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    with_workspace(p)
    p.add_argument("--id", help="run id, e.g. r2-png-read, gate-png-read-1, png-read-long1")
    p.add_argument("--harness", help="harness id")
    kind = p.add_mutually_exclusive_group()
    kind.add_argument("--time", type=float, help="timed run: fuzzing time in seconds (none: until stopped)")
    kind.add_argument("--timeout", type=float, help="self-ending run (replay, merge, reproduction): kill limit in seconds")
    p.add_argument("--copies", type=int, help="run N copies of the command (default 1)")
    p.add_argument("--env", action="append", metavar="K=V", help="environment variable for the command (repeatable)")
    p.add_argument("--findings", action="append", metavar="DIR", help="where the command writes findings (repeatable; default {run}/artifacts/copy-{copy})")
    p.add_argument("--corpus", help="the command's output corpus directory (default {run}/corpus)")
    p.add_argument("--tree", action="append", help="a tree the run's binaries come from (repeatable; default: the repository)")
    p.add_argument("--spec", help="start from a spec.json instead (different commands per copy)")
    p.add_argument("--prepare", action="store_true", help="only write runs/<id>/spec.json and print the command that starts it")
    p.add_argument("--detach", action="store_true", help="return at once; a supervisor keeps the run going "
                   "(refused where process identities can't be read, e.g. in a sandbox)")
    p.add_argument("--skip-cpu-check", action="store_true", help="start even if it needs more cores than are free")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- <command and arguments>")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("wait", help="wait for a run to end")
    with_workspace(p)
    p.add_argument("id")
    p.add_argument("--max", type=float, help="wait at most this many seconds")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("status", help="list runs, or show one")
    with_workspace(p)
    p.add_argument("id", nargs="?")
    p.add_argument("--all", action="store_true", help="also list ended develop runs")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("stop", help="stop a run (exit 1, and not recorded as stopped, if a process is left)")
    with_workspace(p)
    p.add_argument("id")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("_supervise")
    with_workspace(p)
    p.add_argument("id")
    p.set_defaults(func=cmd_supervise)
    return parser


def main(argv=None, scrub: bool = False) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.argv = list(argv) if argv is not None else sys.argv[1:]
    if scrub and args.cmd == "start":
        reexec_scrubbed(__file__, args.argv)
    try:
        return args.func(args)
    except LauncherError as error:
        say("error: %s" % error, sys.stderr)
        return EXIT_USAGE
    except (OSError, ValueError) as error:
        say("error: %s" % error, sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(scrub=True))
