const { readData } = require("../src/tools");

module.exports.fuzz = function (data) {
  readData(data.toString());
};
