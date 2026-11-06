// A record reader whose class method and callbacks Istanbul names
// "(anonymous_<n>)" in lcov.
class Reader {
  constructor(buf) {
    this.buf = buf;
  }

  parse() {
    const fields = this.buf.toString("latin1").split(",");
    if (fields.length > 3) {
      return null;
    }
    return fields.map((f) => f.trim()).filter((f) => f.length > 0);
  }
}

function count(buf) {
  return new Reader(buf).parse()?.length ?? 0;
}

module.exports = { Reader, count };
