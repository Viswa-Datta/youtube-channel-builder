// Parity test: the TypeScript estimator (cost modal) must equal the Python estimator (worker/ledger).
// Run: npx tsx --test tests/pricing.test.ts   (expects python3 + worker importable)
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { estimateWith } from "../lib/pricing-core.ts";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const P = JSON.parse(readFileSync(join(repo, "config", "pricing.json"), "utf8"));

const cases: [string, string, number][] = [];
for (const a of ["channel_kit", "channel_kit_item", "ideas", "production_pack", "whole_video", "full", "shorts", "animated_short"])
  for (const t of ["free", "standard", "premium"]) for (const m of [8, 12, 20, 30]) cases.push([a, t, m]);

test("TS estimator matches Python estimator for every action/tier/length", () => {
  const py = execFileSync("python3", ["-c", `
import json,sys
from ycb import pricing
cases=json.loads(sys.argv[1])
print(json.dumps([pricing.estimate(a,t,m) for a,t,m in cases]))`, JSON.stringify(cases)],
    { cwd: join(repo, "worker"), env: { ...process.env, PYTHONPATH: `${process.env.VENDOR ?? ""}:${join(repo, "worker")}` } });
  const want = JSON.parse(py.toString());
  cases.forEach(([a, t, m], i) => {
    const got = estimateWith(P, a as never, t as never, m);
    assert.equal(got.total_usd, want[i].total_usd, `${a}/${t}/${m} total`);
    assert.equal(got.lines.length, want[i].lines.length);
    got.lines.forEach((l, k) => {
      assert.equal(l.cost_usd, want[i].lines[k].cost_usd, `${a}/${t}/${m} ${l.stage}`);
      assert.equal(l.units, want[i].lines[k].units, `${a}/${t}/${m} ${l.stage} units`);
    });
    assert.equal(got.math, want[i].math);
  });
});

test("free tier is $0 and every paid line shows a free alternative", () => {
  for (const m of [8, 12, 20, 30]) {
    assert.equal(estimateWith(P, "full", "free", m).total_usd, 0);
    for (const l of estimateWith(P, "full", "standard", m).lines) if (l.paid) assert.ok(l.free_alternative.length > 0, l.stage);
  }
});

test("standard stays under $5 per 10 minutes (MVP guardrail)", () => {
  for (const m of [8, 12, 20, 30]) assert.equal(estimateWith(P, "full", "standard", m).guardrail_violation, null);
});
