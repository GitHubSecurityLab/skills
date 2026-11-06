# Jazzer.js toolchain and install plan

## Check without running project code

Run the tools from outside the repository. The check runs before the
first-build notice, so it must not run project files: inside the
repository, Yarn runs the release that `.yarnrc` or `.yarnrc.yml` names
(`yarnPath`), and Corepack and Volta fetch the versions that
`package.json` pins.

<!-- ci: js-toolchain-check -->
```sh
(cd / && node --version && npm --version)
(cd / && yarn --version)     # or pnpm: the package manager of the project's lockfile
```

- Node.js 24 LTS (20.11 at least: ESM hooks and detectors need it). Node 26
  deprecates `module.register()`, which Jazzer.js uses to instrument ES
  modules, so pin 24 even after 26 becomes LTS. Check the project's
  `engines` field too.
- The project's package manager (`npm`, `pnpm`, `yarn`), from its lockfile.

On Linux, the C library too: Jazzer.js 4.0.0 ships only prebuilt native
addons, which need glibc 2.38 or later (Ubuntu 24.04, Debian 13, Fedora 39
and later; none for musl, as on Alpine). It can't build them from source.
<!-- ci: js-glibc-check -->
```sh
[ "$(uname)" != Linux ] || ldd --version 2>&1 | awk 'NR == 1 { v = $NF; split(v, p, ".")
  print "glibc " v ((p[1] > 2 || p[2] >= 38) ? "" : ": too old for Jazzer.js, which needs 2.38") }'
```
If the glibc is older: fuzz in the codespace or in a `node:24-trixie`
container.

## Install plan

List only what's missing. Times were measured on a clean Ubuntu 24.04 with
a fast network; slower machines take a few times longer.

| What | When | Linux | macOS (Homebrew) | Time |
|---|---|---|---|---|
| Node 24 through fnm, at user level | Node missing, or not 24 | `curl -fsSL https://fnm.vercel.app/install \| bash -s -- --skip-shell`, then `~/.local/share/fnm/fnm install 24` | `brew install fnm`, then `fnm install 24` | seconds |
| pnpm or yarn | the lockfile needs it | `corepack enable` (bundled up to Node 24), or `npm install -g pnpm` | same | seconds |
| Jazzer.js | always | dev dependencies (`build.md`) | same | a minute |

Ubuntu's own `nodejs` package (18) is too old. Node's `bin` directory, for
`PATH` in the agent's commands: `fnm exec --using=24 -- which node`.
