#!/usr/bin/env node
// Points git at the committed hooks in scripts/git-hooks/ (core.hooksPath), so
// the pre-commit check (scripts/check-committed-files.mjs --staged) runs on
// every commit. Run by the root package.json "prepare" script (npm install,
// npm ci) and by bin/dev. Idempotent and quiet: it only writes the setting when
// it is unset, and it never fails an install (no git, not a checkout, a
// different hooksPath someone chose on purpose: it says so and leaves it).

import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HOOKS = "scripts/git-hooks";
const repo = realpathSync(resolve(dirname(fileURLToPath(import.meta.url)), ".."));

function git(...args) {
  const r = spawnSync("git", args, { cwd: repo, encoding: "utf8" });
  return r.error ? null : { status: r.status, out: (r.stdout || "").trim() };
}

const top = git("rev-parse", "--show-toplevel");
if (!top || top.status !== 0 || realpathSync(top.out) !== repo) {
  // No git, or not inside this repository's checkout (a tarball, a container build).
  process.exit(0);
}
const current = git("config", "--get", "core.hooksPath");
const value = current && current.status === 0 ? current.out : "";
if (value === HOOKS) process.exit(0);
if (value) {
  console.warn(
    `install-git-hooks: core.hooksPath is "${value}", not ${HOOKS}; leaving it. ` +
      `Run \`git config core.hooksPath ${HOOKS}\` to use the repository's pre-commit check (docs/repo-hygiene.md).`,
  );
  process.exit(0);
}
const set = git("config", "core.hooksPath", HOOKS);
if (set && set.status === 0) {
  console.log(`install-git-hooks: core.hooksPath = ${HOOKS} (pre-commit runs scripts/check-committed-files.mjs --staged)`);
} else {
  console.warn("install-git-hooks: could not set core.hooksPath; run `git config core.hooksPath scripts/git-hooks`");
}
