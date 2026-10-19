#!/usr/bin/env node
// Rebuild site_kg/vendor/three-viewer.min.js:
//   npm install three@0.186.0 esbuild   (in a scratch dir)
//   node tools/build_viewer_bundle.mjs <scratch-dir-with-node_modules>
//
// The output is committed and pinned to three r186. Archives and the live
// viewer load it inline, so they stay offline (file://, single file, no CDN).
// Contributors do not need npm for normal development; only rerun this script
// when deliberately upgrading the pinned three.js release.
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
