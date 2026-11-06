# Feeding input and side effects

How the harness hands the input to the target, and how the target's side
effects stay real while the machine stays clean.

## Feed the input the cheapest way that still reaches the real code

Take the first option that fits. Prefer the buffer API only when the file
handling isn't part of what the attacker reaches; when it is, fuzz it as
deployments run it (below, "When the I/O itself is attack surface").

1. **A buffer API**, when one exists. Fastest and deterministic.

   | Library | Uses the disk | In memory |
   |---|---|---|
   | libarchive | `archive_read_open_filename`, `archive_read_extract` | `archive_read_open_memory`, `archive_read_data` |
   | libxml2 | `xmlReadFile` | `xmlReadMemory` |
   | libpng | `png_init_io(FILE*)` | `png_set_read_fn` with a read callback |
   | Java | `new ZipFile(path)` | `new ZipInputStream(new ByteArrayInputStream(data))` |
   | Node (yauzl) | `yauzl.open(path)` | `yauzl.fromBuffer(buf)` |

2. **A RAM-backed file or handle**, when the API needs one:

   | The API takes | Linux, including codespaces | macOS |
   |---|---|---|
   | `FILE*` | `fmemopen(data, size, "rb")` | `fmemopen`; for empty input, which it refuses, `/dev/null` |
   | A file descriptor | `memfd_create()`, rewound (`lseek`) for each input | a pipe if it only reads sequentially and inputs fit its 64 KiB buffer (written before the call), else option 3 |
   | A path, no extension needed | `memfd_create()`, then `/proc/self/fd/<n>` | option 3 |
   | A path whose extension matters | a file in `/dev/shm/seclab-<harness>-<pid>/input.<ext>`, one directory per process (Docker's default `/dev/shm` is 64 MB) | option 3 |
   | Java `java.nio.file.Path` | an in-memory file system such as Jimfs (test dependency) | same |
   | JS `fs` calls | an in-memory `fs` such as `memfs`, if the API takes one (Jest tests: `jest.mock('fs')`); Jazzer.js's `fs` detector doesn't see its calls | option 3 |

3. **One fixed file in the copy's scratch** (its working directory, or
   `$TMPDIR`), rewritten for each input, never created and deleted per
   input. Each copy of a run has its own scratch.

If the code can notice a RAM-backed file (no file extension on
`/proc/self/fd/<n>`, Jimfs paths aren't `java.io.File`s, different file
metadata) and that matters: prefer option 3.

## When the I/O itself is attack surface

When the file handling is part of what the attacker reaches (extraction
and writes with input-derived paths, external fetches such as XML external
entities, URL resolvers and includes, processes and deletes driven by
input), fuzz it as deployments run it:

- **Don't switch it off** when deployments have it on by default: that
  hides the bug. Switch a feature off only when the threat model rules it
  out.
- **Don't change its behaviour beforehand:** no path check that refuses a
  write, no resolver that fails a fetch, no stub for a process or delete.
  A stub reports success without the effect, so code that needs the
  effect reaches states real use never reaches: false findings, missed
  code.
- **Detection comes from the engines' detectors** (`fuzz-java`'s
  harness.md, "Bug detectors", and `fuzz-js`'s harness.md, "Bug
  detectors"). What they don't see goes unreported; C/C++ has no detector for
  files, processes or the network.
- **The effects are real:** files are written and commands run. Where to
  run is the user's choice, after the first-build notice.

## Keep the machine clean

Point what the harness configures into scratch, keep external services
local, and clean up after each input.

- Every location the harness configures (output, cache, log, database,
  temp directories) points into scratch, never to a path taken from input.
- External services (databases, HTTP backends) become in-process fakes or a
  service on localhost. A fake keeps the oracle: an in-memory H2 or SQLite
  that parses the query, not a stub that accepts anything and hides the
  SQL injection.
- Java and JS: keep the engines' network detectors on (SSRF). Allow only
  the localhost services the harness needs.
- Clean up what the input created in scratch after each input; leftover
  files also break determinism.
- The gate's working-tree comparison catches files written into the
  repository.
