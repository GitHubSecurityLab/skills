const { parseRecord } = require("./record");

describe("record", () => {
  it.fuzz("parse", (data) => {
    parseRecord(data);
  });
});
