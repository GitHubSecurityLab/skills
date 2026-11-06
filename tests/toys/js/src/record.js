// Planted bug for the CI scenarios: the field index from the input isn't
// checked against the number of fields.
function parseRecord(buf) {
  if (buf.length < 5 || buf.toString("latin1", 0, 4) !== "REC1") return null;
  const index = buf[4];
  const fields = buf.toString("latin1", 5).split(",");
  return fields[index].trim();
}

module.exports = { parseRecord };
