const { trimNewlines } = require("../src/tools");

module.exports.fuzz = function (data) {
  trimNewlines(data.toString());
};
