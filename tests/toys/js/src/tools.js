const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const DATA = path.join(__dirname, "..", "data");

// Both slip past Jazzer.js's detectors, which check only the first argument:
// the command is "sh", and the path is already resolved.
function greet(name) {
  return spawnSync("sh", ["-c", "echo hello " + name]).status;
}

function readData(name) {
  try {
    return fs.readFileSync(path.resolve(DATA, name));
  } catch {
    return null;
  }
}

// Quadratic backtracking on a long run of newlines that ends in another
// character (trim-newlines, CVE-2021-33623): far past the fuzzer's sizes.
function trimNewlines(text) {
  return text.replace(/[\r\n]+$/, "");
}

module.exports = { greet, readData, trimNewlines };
