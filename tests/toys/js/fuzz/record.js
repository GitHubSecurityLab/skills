const { parseRecord } = require("../src/record");

module.exports.fuzz = function (data) {
  parseRecord(data);
};
