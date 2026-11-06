// An ES module (the package's "type"), fuzzing the compiled TypeScript.
import { parseRecord } from "../.seclab/fuzz/build/ts/src/record.js";

export function fuzz(data) {
  parseRecord(data);
}
