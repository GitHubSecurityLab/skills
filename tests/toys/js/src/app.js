// Planted bug for the CI scenarios: a record without "fields" makes the
// handler throw; like Express, the server answers an exception with a 500.
module.exports = function app(req, res) {
  let body = "";
  req.on("data", (chunk) => (body += chunk));
  req.on("end", () => {
    try {
      const record = JSON.parse(body);
      res.end(JSON.stringify({ count: record.fields.length }));
    } catch (e) {
      res.statusCode = e instanceof SyntaxError ? 400 : 500;
      res.end();
    }
  });
};
