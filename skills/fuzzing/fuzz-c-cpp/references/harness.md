# C/C++ harnesses

## `LLVMFuzzerTestOneInput`

libFuzzer and AFL++ both call this entry point, once per input:

```c
#include <stddef.h>
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  /* call the entry point with data/size */
  return 0;
}
```

- C++ files declare it `extern "C"`.
- Return 0. `-1` tells libFuzzer not to add the input to the corpus; other
  values are reserved.
- Handle every input, including empty ones. Never `exit()`; join any
  threads you start.
- C++: never `catch (...)` (`fuzz-harness`, "Design the harness"). An
  uncaught exception is a `deadly signal` with the throw's stack.
- `data` is read-only and exactly `size` bytes long. If the API needs a
  NUL-terminated or writable buffer, copy the input into an exact-size
  heap buffer, so that ASan still catches over-reads:
  ```c
  char *s = malloc(size + 1);
  if (!s) return 0;
  memcpy(s, data, size);
  s[size] = '\0';
  parse_string(s);
  free(s);
  ```
- One-time setup: `int LLVMFuzzerInitialize(int *argc, char ***argv)`, or a
  static flag in the harness. Don't read configuration from `argv`.
- Limit the size to what the format needs (`if (size > MAX) return 0;`).
  Record `MAX` as the harness's input cap in the target file: every
  libFuzzer command passes it as `-max_len` (`run.md`).
- Docs: https://llvm.org/docs/LibFuzzer.html#fuzz-target

## Splitting the input

C++: `FuzzedDataProvider` splits one input into as many typed values as
the harness needs. Its header ships with clang, so no `-I` is needed. For
example, the following harness parses a document and takes two options,
so the options come from a prefix:

```cpp
#include <fuzzer/FuzzedDataProvider.h>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  if (size < 8) return 0;  // shorter than the options prefix
  FuzzedDataProvider options(data, 8);  // the options' bytes, plus spare
  int mode = options.ConsumeIntegralInRange<int>(0, 2);  // a choice deployments make
  bool strict = options.ConsumeBool();
  std::vector<uint8_t> doc(data + 8, data + size);  // the rest
  parse(doc.data(), doc.size(), mode, strict);
  return 0;
}
```

- Where `FuzzedDataProvider`'s values come from in the input, and why a
  document's options come from a prefix: `fuzz-harness`, "Design the
  harness".
- `doc` is an exact-size copy, so reads past either end still trip ASan.
- Header reference:
  https://github.com/llvm/llvm-project/blob/main/compiler-rt/include/fuzzer/FuzzedDataProvider.h

If the harness is C: the same, but read the options from the prefix's
bytes directly.

## Call sequences

A stateful API (create, configure, use, destroy): the bounded call loop in
`call-sequences.md` next to this file.

Callbacks return what the input picks (`fuzz-harness`, "Design the
harness").

## Signalling a failed check

A property check that fails prints what broke and calls `abort()`:

```c
if (decoded_len != expected_len) {
  fprintf(stderr, "property violated: round-trip length %zu != %zu\n",
          decoded_len, expected_len);
  abort();
}
```

`assert()` disappears with `NDEBUG`; use an explicit check. The message
helps triage group property violations.

## Feeding files, handles and paths

See `input-and-side-effects.md` in `fuzz-harness` for which option to use.
C idioms:

```c
/* FILE*: macOS's fmemopen refuses size 0 */
FILE *f = size ? fmemopen((void *)data, size, "rb") : fopen("/dev/null", "rb");
if (!f) return 0;
parse_file(f);
fclose(f);
```

```c
/* Linux: a path backed by memory. Create the memfd once, rewrite it per input. */
static int fd = -1;
static char path[64];
if (fd < 0) {
  fd = memfd_create("fuzz-input", 0);
  snprintf(path, sizeof path, "/proc/self/fd/%d", fd);
}
ftruncate(fd, 0);
pwrite(fd, data, size, 0);
parse_path(path);  /* an API taking the fd itself: lseek(fd, 0, SEEK_SET) first */
```

## Where harnesses live

Common places: `fuzz/`, `tests/fuzz/`, `test/fuzzing/`, `oss-fuzz/`, or a
separate repository (`curl/curl-fuzzer`). File names are often
`<thing>_fuzzer.c`. Register targets in the build the way their
neighbours are registered. If the project is in OSS-Fuzz, its `build.sh`
(`google/oss-fuzz/projects/<name>/`) shows which files are harnesses, and
where seed corpora and dictionaries come from.

## Other formats

- **FuzzTest** (`FUZZ_TEST`): see `fuzztest.md` next to this file.
- **Native AFL++** harnesses: `aflpp.md` next to this file.
- **Custom mutators** (`LLVMFuzzerCustomMutator`) keep compressed,
  checksummed or grammar-bound input valid: they decode the input, mutate
  its structure and re-encode it
  (https://github.com/google/fuzzing/blob/master/docs/structure-aware-fuzzing.md).
  Structure-aware escalation: `fuzz-coverage`.
  - A FuzzTest domain can replace a custom mutator; a `FuzzedDataProvider`
    split can't.
  - AFL++'s custom-mutator API differs, so the long runs of a harness
    with an `LLVMFuzzerCustomMutator` use libFuzzer.
  - libprotobuf-mutator's `DEFINE_PROTO_FUZZER` harnesses are kept as they
    are, and their long runs use libFuzzer too.
