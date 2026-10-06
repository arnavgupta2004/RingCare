// Accessibility check (axe-core, WCAG 2.2 A/AA) + full-page screenshots of both views.
// Needs the backend (:8000) and `npm run dev` (:5180) running.
//   node scripts/a11y-and-screenshots.mjs [--only resident|caregiver] [baseUrl]
// Exits non-zero if /resident has any axe violation.
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";
import { AxeBuilder } from "@axe-core/playwright";

const args = process.argv.slice(2);
const onlyIdx = args.indexOf("--only");
const only = onlyIdx >= 0 ? args.splice(onlyIdx, 2)[1] : null;
const base = args[0] ?? "http://localhost:5180";
const outDir = new URL("../../docs/screenshots/", import.meta.url);
await mkdir(outDir, { recursive: true });

const pages = [
  { path: "/resident", name: "resident", viewport: { width: 900, height: 1000 }, mustPass: true },
  { path: "/resident", name: "resident-phone", viewport: { width: 390, height: 844 }, mustPass: true },
  { path: "/caregiver", name: "caregiver", viewport: { width: 1280, height: 900 }, mustPass: false },
];

const browser = await chromium.launch();
let failed = false;
for (const p of pages.filter((p) => !only || p.path === `/${only}`)) {
  const context = await browser.newContext({ viewport: p.viewport, deviceScaleFactor: 2 });
  const page = await context.newPage();
  await page.goto(base + p.path, { waitUntil: "networkidle" });
  await page.waitForSelector("main h2");
  await page.waitForFunction(() => [...document.images].every((img) => img.complete));

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"])
    .analyze();
  const file = new URL(`${p.name}.png`, outDir);
  await page.screenshot({ path: file.pathname, fullPage: true });

  console.log(`\n${p.name} (${p.path}): ${results.passes.length} rules passed, ${results.violations.length} violation(s), ` +
    `${results.incomplete.length} need manual review`);
  for (const v of results.violations) {
    console.log(`  [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} node(s))`);
    for (const n of v.nodes.slice(0, 3)) console.log(`      ${n.target.join(" ")}  ${n.failureSummary?.split("\n")[1] ?? ""}`);
  }
  for (const i of results.incomplete) console.log(`  [review] ${i.id}: ${i.help} (${i.nodes.length} node(s))`);
  console.log(`  screenshot: ${file.pathname}`);
  if (p.mustPass && results.violations.length) failed = true;
  await context.close();
}
await browser.close();
process.exit(failed ? 1 : 0);
