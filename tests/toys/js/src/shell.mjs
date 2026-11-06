// A named import of a built-in, bound when the module loads.
import { execSync } from "node:child_process";

export function hello(name) {
  try {
    return execSync("echo hello " + name, { stdio: "ignore" });
  } catch {
    return null;
  }
}
