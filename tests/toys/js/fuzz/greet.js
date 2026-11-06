const { greet } = require("../src/tools");

module.exports.fuzz = function (data) {
  greet(data.toString());
};
