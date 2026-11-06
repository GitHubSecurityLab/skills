# Threat model file structure

Keep entries short; link to code with `path:line`. Every entry ends with
its provenance. Mark guesses as `(assumption)`.

```markdown
# Threat model: <project>

Base commit: <sha> (<branch or tag>) · Updated: <date>

## Context
- **What it is:** <one sentence> [doc: README.md]
- **Deployment:** <library in servers / CLI on untrusted files / ...>
- **Attacker:** <who supplies input, how>
- **Assets:** <what must be protected: memory safety of the host process,
  files outside a base dir, ...>
- **Trusted:** <operator config, caller's own code, ...>
- **In scope:** <bug classes and impacts>
- **Out of scope:** <e.g. crashes from invalid API use, local config files>

## Sources
| Id | Entry point | Untrusted input | Attacker controls | Bug means | Provenance |
|---|---|---|---|---|---|
| S1 | `png_read_info(png_ptr, info)` after `png_set_read_fn` | PNG file bytes | whole stream | memory error, hang, OOM on small input | [doc: README] |

## Sinks
Call sites go in Location.

| Id | Location | Kind | Engine detector | Reached from | Sanitization on path | Provenance |
|---|---|---|---|---|---|---|
| K1 | `Unzip.writeEntry`: `src/Unzip.java:88` `new FileOutputStream(dest)`, `:95` `Files.copy` | file write (path traversal) | partly | S2 | none found | [code: src/Unzip.java:88] |
| H1 | `pngrutil.c` `png_handle_*` chunk handlers | size arithmetic on chunk lengths | sanitizers | S1 | length checks in png_read_chunk_header | [code: pngrutil.c:160] |

## Paths
- **S2 → K1:** `extract(zip)` → `writeEntry(entry)` → `new FileOutputStream(base + entry.getName())`;
  no canonicalization. [code: src/Unzip.java:40-88]

## Entry point: <name>
(Focused mode: the four questions for one entry point.)
1. Untrusted input:
2. Trust boundary:
3. Attacker controls:
4. A bug means:

## Open questions
- <question the code can't answer> (asked: yes/no)
```
