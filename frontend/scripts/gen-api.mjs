// Regenerate src/lib/api-types.ts from the FastAPI app's OpenAPI schema (no server needed).
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..", "..");
const python = [
  path.join(root, ".venv", "Scripts", "python.exe"),
  path.join(root, ".venv", "bin", "python"),
].find(existsSync);
if (!python) throw new Error("Python virtualenv not found at ../.venv");

const schema = execFileSync(
  python,
  ["-c", "import json; from backend.app.main import create_app; print(json.dumps(create_app().openapi()))"],
  { cwd: root, encoding: "utf8" },
);
const tmp = path.join(mkdtempSync(path.join(tmpdir(), "aise-")), "openapi.json");
writeFileSync(tmp, schema);
execFileSync(
  process.execPath,
  [path.join("node_modules", "openapi-typescript", "bin", "cli.js"), tmp, "-o", "src/lib/api-types.ts"],
  { stdio: "inherit" },
);
