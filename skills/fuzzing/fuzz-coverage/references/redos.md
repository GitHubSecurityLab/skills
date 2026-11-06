# Regular expressions applied to input (Java/JS)

A reached regex without a timeout isn't cleared: fuzzing finds only the
exponential kind of backtracking, as a timeout. Most ReDoS is quadratic
and takes seconds only past about 50,000 characters, far beyond the fuzzer's
inputs. A regex backtracks when one part can match the same text in more
than one way: nested or adjacent quantifiers over overlapping characters
(`(a+)+`, `\s*,\s*$` on a run of spaces), or a repeated class before
something that fails (`[\r\n]+$` on newlines that end in another
character).

1. **List** the regexes of the reached regular-expression groups whose
   subject (the text matched) comes from input, one per line in
   `.seclab/fuzz/sinks/<id>.regex`, tab-separated: the site
   (`file:line`), the flags, and the pattern as the engine sees it (a
   string literal's escapes resolved).
2. **Screen and time** them with `recheck`: it tells which ones backtrack
   and writes an attack for each that should time out. It's optional and
   needs Node.js; it installs under `.seclab/fuzz/tools/`, outside the
   project (in interactive mode, ask first, as for the install plan):
   <!-- ci: redos-check -->
   ```sh
   npm install --prefix "$F/tools/recheck" --ignore-scripts --no-audit --no-fund --loglevel=error recheck@4.5.0
   NODE_PATH="$F/tools/recheck/node_modules" node - "$F/sinks/<id>.regex" "$F/sinks/<id>.redos" <input_timeout_s> <<'EOF'
   const fs = require("fs"), path = require("path"), { checkSync } = require("recheck");
   const [list, out, limit] = process.argv.slice(2);
   for (const line of fs.readFileSync(list, "utf8").split("\n").filter(Boolean)) {
     const [site, flags, source] = line.split("\t");
     const r = checkSync(source, flags);
     if (r.status !== "vulnerable") { console.log(`${site}\t${r.status}`); continue; }
     const { pumps, suffix, base } = r.attack, exp = r.complexity.type === "exponential";
     const attack = (n) => pumps.map((p) => p.prefix + p.pump.repeat(n + p.bias)).join("") + suffix;
     const re = new RegExp(source, flags.replace(/[gy]/g, ""));
     re.test(attack(1)); re.test(attack(1));
     const ns = exp ? [base - 8, base - 6, base - 4] : [base >> 2, base >> 1, base];
     const ms = ns.map((n) => { const t = process.hrtime.bigint(); re.test(attack(n)); return Number(process.hrtime.bigint() - t) / 1e6; });
     let n = ns[2], t = ms[2];
     while (t < 2000 * limit) { n = exp ? n + 2 : 2 * n; t *= Math.max(ms[2] / ms[1], 2); }
     const file = path.join(out, site.replace(/[^\w.-]+/g, "_"));
     fs.mkdirSync(out, { recursive: true });
     fs.writeFileSync(file, attack(n));
     console.log(`${site}\t${r.complexity.type} ${r.complexity.degree || ""}\t` +
       ns.map((m, i) => `${attack(m).length} chars ${Math.round(ms[i])} ms`).join(", ") +
       `\trepro: ${file} (${attack(n).length} chars, about ${Math.round(t / 1000)} s)`);
   }
   EOF
   ```
   Each regex prints `safe`, `unknown` (syntax `recheck` can't parse:
   read it yourself), or its complexity (`polynomial 2` is quadratic, or
   `exponential`), its match times at three attack sizes, and `repro`: the
   attack at a size estimated to take twice the input timeout.
   - If the project is Java: `recheck` and the times follow JavaScript's
     engine; for Java they're a lead.
   - If you don't use `recheck`: read each regex for the shapes above and
     write the attack yourself: a prefix, a repeated part, and a suffix
     that makes the match fail.
3. **Confirm through the harness:** put `repro` into the harness's input
   format (as is, if the harness passes its bytes on as text) and replay it
   as a reproduction (`fuzz-triage`, "Reproduce"), with the command in
   the language skill's triage.md, "Reproduce".
   A timeout there shows that input reaches the regex with the attack
   intact.
   - If it times out: that confirms it; triage it like an external crash
     (`fuzz-triage`, "Collect"); the verdict is `timeout`.
   - If it finishes: Java's engine may be faster, so double the repeated
     part until a replay times out or the time stops growing. A time that
     doesn't grow means a length limit or a filter stops the attack before
     the regex: record the group as sanitized, with what stops it.
