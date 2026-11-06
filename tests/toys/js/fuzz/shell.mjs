// fuzz-js's harness.md: sync the built-ins' named exports, then load the target.
import { syncBuiltinESMExports } from "node:module";
syncBuiltinESMExports();
const { hello } = await import("../src/shell.mjs");

export function fuzz(data) {
  hello(data.toString());
}
