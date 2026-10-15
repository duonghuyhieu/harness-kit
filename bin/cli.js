#!/usr/bin/env node
// harness-kit launcher for npm/npx. Zero dependencies: it finds Python >= 3.10 and forwards every argument to
// harness.py (the real CLI, Python standard library only). `init` is an alias of `install`.
//   npx github:duonghuyhieu/harness install .
//   npx github:duonghuyhieu/harness doctor
"use strict";
const { spawnSync } = require("child_process");
const path = require("path");

const KIT = path.join(__dirname, "..", "harness.py");
const CANDIDATES = process.platform === "win32"
  ? [["python"], ["py", "-3"], ["python3"]]
  : [["python3"], ["python"]];

function findPython() {
  for (const [cmd, ...pre] of CANDIDATES) {
    const probe = spawnSync(cmd, [...pre, "-c", "import sys; print(sys.version_info >= (3, 10))"],
      { encoding: "utf8" });
    if (probe.status === 0 && probe.stdout.trim() === "True") return [cmd, ...pre];
  }
  return null;
}

const args = process.argv.slice(2);
if (args[0] === "init") args[0] = "install";
if (args.length === 0) args.push("--help");

const py = findPython();
if (!py) {
  console.error("harness-kit needs Python >= 3.10 on PATH (python3 / python / py -3).\n" +
    "Install it from https://www.python.org/downloads/ and run this command again.");
  process.exit(1);
}
const res = spawnSync(py[0], [...py.slice(1), KIT, ...args], { stdio: "inherit" });
process.exit(res.status === null ? 1 : res.status);
