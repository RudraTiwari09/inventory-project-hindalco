// Builds the Netlify launch page: copies site/public -> dist and injects the
// Streamlit app URL. Override with the STREAMLIT_APP_URL environment variable.
import { cpSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(root, "site", "public");
const out = join(root, "dist");
const fallback = "https://inventory-project-hindalco.onrender.com";

let appUrl = (process.env.STREAMLIT_APP_URL || fallback).trim().replace(/\/+$/, "");
if (!/^https:\/\/[^\s"'<>]+$/.test(appUrl)) {
  console.warn(`STREAMLIT_APP_URL must be an https:// URL; using ${fallback}`);
  appUrl = fallback;
}

rmSync(out, { recursive: true, force: true });
cpSync(src, out, { recursive: true });
for (const file of ["index.html", "app.js"]) {
  const p = join(out, file);
  writeFileSync(p, readFileSync(p, "utf8").replaceAll("__APP_URL__", appUrl));
}
console.log(`Launch page built into dist/ (app URL: ${appUrl})`);
