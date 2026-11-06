// A named helper before a class whose methods Istanbul names
// "(anonymous_<n>)" in lcov.
function limit(n) {
  return n > 8 ? 8 : n;
}

class Records {
  constructor(buf) {
    this.buf = buf;
  }

  split() {
    const fields = this.buf.toString("latin1").split(";");
    if (fields.length > limit(fields.length)) {
      return [];
    }
    return fields.map((f) => f.trim());
  }

  header() {
    const first = this.split()[0];
    return first ? first.toUpperCase() : "";
  }
}

function load(buf) {
  return new Records(buf).split().length;
}

module.exports = { Records, load };
