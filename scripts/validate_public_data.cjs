"use strict";

const fs = require("fs");
const path = require("path");
const { validateSnapshot, validateProofs } = require(path.join(__dirname, "..", "docs", "validate.js"));

if (process.argv.length !== 4) {
  console.error("usage: node scripts/validate_public_data.cjs SNAPSHOT PROOFS");
  process.exit(2);
}

const readJson = (filename) => JSON.parse(fs.readFileSync(filename, "utf8"));
validateSnapshot(readJson(process.argv[2]));
validateProofs(readJson(process.argv[3]));
console.log("public data semantic validation passed");
