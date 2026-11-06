// Planted bug for the CI scenarios: merge() follows "__proto__" keys of
// parsed JSON into Object.prototype.
function merge(target, source) {
  for (const key of Object.keys(source)) {
    const value = source[key];
    if (value !== null && typeof value === "object") {
      if (typeof target[key] !== "object" || target[key] === null) target[key] = {};
      merge(target[key], value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

module.exports = { merge };
