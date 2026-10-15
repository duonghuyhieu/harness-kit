#!/usr/bin/env node
// `npm install -D github:duonghuyhieu/harness` installs the harness into the project right away (like claudekit).
// Never overwrites a file, never fails `npm install`. Skipped for global installs, for installs inside the kit itself,
// for transitive installs, outside a project with package.json, and when HARNESS_SKIP_INSTALL=1.
"use strict";
const fs = require("fs");
const path = require("path");

function targetDir() {
  if (process.env.HARNESS_SKIP_INSTALL === "1") return null;
  if (process.env.npm_config_global === "true") return null;
  if (process.env.npm_command === "exec") return null;  // npx: the bin itself installs
  const target = process.env.INIT_CWD;
  if (!target) return null;
  if (path.resolve(target) === path.resolve(__dirname, "..")) return null;
  if (target.split(path.sep).includes("node_modules")) return null;
  if (!fs.existsSync(path.join(target, "package.json"))) return null;
  return target;
}

try {
  const target = targetDir();
  if (target) {
    console.log("\nharness-kit: installing the harness into " + target + " ...");
    const code = require("./cli.js").run(["install", target]);
    if (code !== 0) console.log("harness-kit: install did not finish (code " + code + "); run `npx harness install .` later.");
  }
} catch (err) {
  console.error("harness-kit postinstall warning:", err && err.message ? err.message : err);
}
process.exit(0);
