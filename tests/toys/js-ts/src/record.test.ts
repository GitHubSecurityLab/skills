// A project test: the fuzz build leaves it out, or the project's own test run would also pick up the copy.
import { parseRecord } from "./record";

if (parseRecord(new Uint8Array([82, 69, 67, 49, 0, 97])) !== "a") throw new Error("parseRecord");
