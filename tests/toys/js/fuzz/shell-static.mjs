// A static import loads the target before the harness runs.
import { hello } from "../src/shell.mjs";

export function fuzz(data) {
  hello(data.toString());
}
