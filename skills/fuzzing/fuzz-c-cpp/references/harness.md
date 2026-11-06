# C/C++ harnesses

## `LLVMFuzzerTestOneInput`

The entry point libFuzzer and AFL++ both call, once per input:

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
- C++: like `catch (...)` (`fuzz-harness`, "Design the harness"), never
  catch `std::out_of_range` from `.at()`: it's an out-of-bounds index the
  check stopped. An uncaught exception is a `deadly signal` with the
  throw's stack.
- `data` is read-only and exactly `size` bytes long. If the API needs a
  NUL-terminated or writable buffer, copy into an exact-size heap buffer so
  ASan still catches over-reads:
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
- Limit size to what the format needs (`if (size > MAX) return 0;`), and
  record `MAX` as the harness's input cap in the target file: every
  libFuzzer command passes it as `-max_len` (`run.md`).
- Docs: https://llvm.org/docs/LibFuzzer.html#fuzz-target

## Splitting the input

C++: `FuzzedDataProvider` splits one input into options and a document
(header ships with clang, no `-I` needed):

```cpp
#include <fuzzer/FuzzedDataProvider.h>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  FuzzedDataProvider fdp(data, size);
  int mode = fdp.ConsumeIntegralInRange<int>(0, 2);  // a choice deployments make
  bool strict = fdp.ConsumeBool();
  std::vector<uint8_t> doc = fdp.ConsumeRemainingBytes<uint8_t>();
  parse(doc.data(), doc.size(), mode, strict);
  return 0;
}
```

- Integers, booleans and enums are taken from the **end** of the input;
  bytes and strings from the front. A raw format file used as a seed loses
  its last bytes to the options, which breaks formats whose end matters
  (JSON, XML, ZIP's end record, PNG's `IEND`). For such formats, take the
  options from a fixed-size prefix or from bytes after the document, or
  write the seeds in the harness's layout (`fuzz-harness`, seeds.md).
- Copies from `ConsumeBytes`/`ConsumeRemainingBytes` are exact-size, so
  over-reads still trip ASan.
- Header reference:
  https://github.com/llvm/llvm-project/blob/main/compiler-rt/include/fuzzer/FuzzedDataProvider.h

If the harness is C: take option bytes the same way, then pass the rest.

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
separate repository (`curl/curl-fuzzer`); file names are often
`<thing>_fuzzer.c`. Register targets in the build as the neighbours are.
If the project is in OSS-Fuzz: its `build.sh`
(`google/oss-fuzz/projects/<name>/`) shows which files are harnesses and
where seed corpora and dictionaries come from.

## Other formats

- **FuzzTest** (`FUZZ_TEST`): see `fuzztest.md` next to this file.
- **Native AFL++** harnesses: `aflpp.md` next to this file.
- **Custom mutators** (`LLVMFuzzerCustomMutator`: decode, mutate the
  structure, re-encode; structure-aware escalation in `fuzz-coverage`)
  keep compressed, checksummed or grammar-bound input valid
  (https://github.com/google/fuzzing/blob/master/docs/structure-aware-fuzzing.md).
  A FuzzTest domain can replace one; a `FuzzedDataProvider` split can't.
  AFL++'s custom-mutator API differs, so such a harness's long runs use
  libFuzzer, as do libprotobuf-mutator's `DEFINE_PROTO_FUZZER` harnesses,
  kept as they are.
