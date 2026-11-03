// Copies the shared config (also read by the Python worker) into the web app before dev/build,
// so Vercel builds with Root Directory = web never import files from outside the project.
import { cpSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
mkdirSync(join(here, "..", "config"), { recursive: true });
for (const f of ["pricing.json", "plans.json"]) cpSync(join(here, "..", "..", "config", f), join(here, "..", "config", f));
console.log("config copied");
