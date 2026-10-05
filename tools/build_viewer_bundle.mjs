#!/usr/bin/env node
// Rebuild site_kg/vendor/three-viewer.min.js:
//   npm install three esbuild   (in a scratch dir)
//   node tools/build_viewer_bundle.mjs <scratch-dir-with-node_modules>
// Output is committed; contributors do not need node for normal development.
import { execFileSync } from "node:child_process";
import { writeFileSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
const cwd = process.argv[2] || ".";
// entry must live inside cwd so esbuild resolves `three` from its node_modules
const entry = join(cwd, ".t3_entry.mjs");
writeFileSync(entry, 'export * as THREE from "three";\n' +
  'export { OrbitControls } from "three/addons/controls/OrbitControls.js";\n');
execFileSync(join(cwd, "node_modules/.bin/esbuild"), [entry, "--bundle", "--minify",
  "--format=iife", "--global-name=T3", "--outfile=" + join(REPO, "site_kg/vendor/three-viewer.min.js")],
  { cwd, stdio: "inherit" });
rmSync(entry);
