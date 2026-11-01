// Runs one of the offline TypeScript scripts (layoutcheck.ts, layoutsvg.ts) on any Node 18+.
//   node scripts/run-ts.mjs scripts/layoutcheck.ts snaps.json 900
// Node 20 has no --experimental-strip-types (the v6.6 bench printed "layout: skipped" on a Mac with Node 20), so this
// transpiles the script and the local .ts files it imports with the project's own TypeScript into
// node_modules/.cache/minutecanvas-ts (packages like @dagrejs/dagre still resolve from node_modules) and runs it.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import ts from "typescript";

const ROOT = resolve(dirname(new URL(import.meta.url).pathname), "..");
const OUT = join(ROOT, "node_modules", ".cache", "minutecanvas-ts");
const SPEC = /(\bfrom\s*|\bimport\s*\(?\s*)(["'])(\.{1,2}\/[^"']+)\2/g;

function build(file, done) {
  if (done.has(file)) return;
  done.add(file);
  const src = readFileSync(file, "utf8");
  const deps = [];
  const js = ts
    .transpileModule(src, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, verbatimModuleSyntax: false },
      fileName: file,
    })
    .outputText.replace(SPEC, (m, pre, q, spec) => {
      const target = resolve(dirname(file), spec.endsWith(".ts") ? spec : `${spec}.ts`);
      deps.push(target);
      return `${pre}${q}${spec.replace(/\.ts$/, "")}.mjs${q}`;
    });
  // .mjs: Node before 20.19 loads a plain .js file outside a "type": "module" package as CommonJS
  const out = join(OUT, relative(ROOT, file)).replace(/\.ts$/, ".mjs");
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, js);
  for (const d of deps) build(d, done);       // type-only imports are already gone from the output
}

const entry = resolve(process.argv[2] ?? "");
if (!entry.endsWith(".ts")) {
  console.error("usage: node scripts/run-ts.mjs scripts/<name>.ts [args...]");
  process.exit(2);
}
build(entry, new Set());
process.argv = [process.argv[0], entry, ...process.argv.slice(3)];   // the script sees its own name in argv[1]
await import(pathToFileURL(join(OUT, relative(ROOT, entry)).replace(/\.ts$/, ".mjs")).href);
