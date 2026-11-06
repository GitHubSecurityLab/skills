// Reproducer: parseRecord() trusts the field index from the input.
const assert = require("node:assert");
const { parseRecord } = require(process.env.PKG + "/src/record");

assert.strictEqual(parseRecord(Buffer.from("REC1\x05a,b", "latin1")), null);
