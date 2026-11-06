# C/C++ toolchain and install plan

## Check without running project code

clang, `lld`, the sanitizer and libFuzzer runtimes, `llvm-profdata`,
`llvm-cov` and `llvm-symbolizer` must all be the same LLVM version, from
`<llvm_bin>`: `/usr/lib/llvm-20/bin` on Ubuntu, `/opt/homebrew/opt/llvm/bin`
on macOS. Check their versions, and that a libFuzzer binary links:

<!-- ci: c-toolchain-check -->
```sh
<llvm_bin>/clang --version; <llvm_bin>/llvm-cov --version; <llvm_bin>/llvm-profdata --version
printf 'extern "C" int LLVMFuzzerTestOneInput(const unsigned char*, unsigned long){return 0;}' \
  | <llvm_bin>/clang++ -x c++ -fsanitize=fuzzer,address - -o .seclab/fuzz/build/toolchain-check && echo libFuzzer ok
rm -f .seclab/fuzz/build/toolchain-check
afl-fuzz --version         # AFL++, if used
```

## Install plan

List only what's missing, for the project's build system. Run **`sudo`**
only in a disposable environment (`fuzz-harness`'s build.md); otherwise
print the command for the user. On Ubuntu, run `sudo apt-get update`
before the first install: fresh images, the devcontainer included, ship
without package lists.

| What | When | Ubuntu 24.04 | macOS (Homebrew) | Time |
|---|---|---|---|---|
| LLVM 20: clang, `lld`, runtimes, tools | always | `sudo apt-get install -y clang-20 lld-20 llvm-20 libclang-rt-20-dev`; tools in `/usr/lib/llvm-20/bin` | `brew install llvm` (needs the Xcode Command Line Tools); keg-only, use `$(brew --prefix llvm)/bin/clang` | under a minute |
| CMake | `CMakeLists.txt` | `sudo apt-get install -y cmake ninja-build` | `brew install cmake ninja` | seconds |
| Meson | `meson.build` | `sudo apt-get install -y meson ninja-build` | `brew install meson` | seconds |
| autotools | `configure.ac` | `sudo apt-get install -y autoconf automake libtool pkg-config make` | `brew install autoconf automake libtool pkg-config` | seconds |
| Bazel | `MODULE.bazel`, `WORKSPACE` | Bazelisk as `~/.local/bin/bazel` (below) | `brew install bazelisk` | seconds; the first build downloads Bazel |
| AFL++ | Linux long runs | `sudo apt-get install -y build-essential llvm-20-dev`, then build a release (`aflpp.md`) | optional: `brew install afl++` | 1–3 minutes |
| The project's own dependencies | its README, CI files, OSS-Fuzz `Dockerfile` | as they say | as they say | varies |

Times were measured on a clean Ubuntu 24.04 with a fast network; slower
machines take a few times longer.

- **LLVM 20 on Linux:** FuzzTest needs it (`fuzztest.md`), and libFuzzer,
  AFL++ 5.03c and the coverage build work with it, so one version serves
  every engine. Apple's clang has no libFuzzer runtime.
- **AFL++ on Linux:** Ubuntu's `afl++` package is 4.09c, far behind: build
  a release instead.

Bazelisk on Linux:

<!-- ci-skip: installs Bazelisk into ~/.local/bin; there's no Bazel toy -->
```sh
mkdir -p ~/.local/bin
curl -fsSL -o ~/.local/bin/bazel \
  "https://github.com/bazelbuild/bazelisk/releases/download/v1.30.0/bazelisk-linux-$(dpkg --print-architecture)"
chmod +x ~/.local/bin/bazel
```
