// Planted bug for the CI scenarios: parse() promises plain data keys and drops "__proto__",
// but keeps other keys that shadow Object.prototype ("toString=x").
function parse(query) {
  const result = {};
  for (const pair of query.split("&")) {
    const [key, value = ""] = pair.split("=");
    if (key && key !== "__proto__") result[key] = value;
  }
  return result;
}

module.exports = { parse };
