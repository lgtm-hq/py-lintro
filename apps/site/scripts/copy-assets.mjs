/**
 * Pre-build: copy shared assets into public/.
 */
import { cpSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(__dirname, "..");
const repoRoot = resolve(siteRoot, "..", "..");
const publicAssets = join(siteRoot, "public", "assets");
const _publicDir = join(siteRoot, "public");

mkdirSync(publicAssets, { recursive: true });

const logo = join(repoRoot, "assets", "images", "lintro.png");
if (existsSync(logo)) {
  cpSync(logo, join(publicAssets, "lintro.png"));
  console.log("Copied lintro.png");
}

const turboCssRoot = join(
  siteRoot,
  "node_modules",
  "@lgtm-hq",
  "turbo-themes",
  "packages",
  "css",
  "dist",
);

function copyDir(src, dest) {
  if (!existsSync(src)) return;
  mkdirSync(dest, { recursive: true });
  for (const entry of readdirSync(src, { withFileTypes: true })) {
    const srcPath = join(src, entry.name);
    const destPath = join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDir(srcPath, destPath);
    } else {
      cpSync(srcPath, destPath);
    }
  }
}

if (existsSync(turboCssRoot)) {
  copyDir(turboCssRoot, join(publicAssets, "css"));
  console.log("Copied turbo-themes CSS");
} else {
  throw new Error("turbo-themes CSS not found — run bun install in apps/site first");
}
