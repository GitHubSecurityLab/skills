// A harness with state leaking between inputs, for the gate's leak check:
// only inputs after the first in a process take this path.
const { parseRecord } = require("../src/record");

let calls = 0;

function stalePath() {
  return calls * 2;
}

module.exports.fuzz = function (data) {
  if (calls++ > 0) stalePath();
  parseRecord(data);
};
