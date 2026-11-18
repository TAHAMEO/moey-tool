// Browser smoke test against a running CLIPFORGE (Docker stack or dev servers).
//
//   npm i --no-save playwright && npx playwright install chromium
//   BASE=http://localhost:3000 OWNER_EMAIL=you@example.com OWNER_PASSWORD='…' node e2e/smoke.mjs
//   VIDEO_FILE=/path/to/talk.mp4 … node e2e/smoke.mjs     # also upload and process a real video
//
// Creates the owner account on a fresh install (otherwise signs in), opens every page on desktop
// and phone sizes, and fails on page errors, HTTP 5xx responses or horizontal scrolling on phones.
// With VIDEO_FILE it uploads the file in Brain Chat, waits for the real pipeline and checks that
// the rendered clip plays. Screenshots go to ./e2e-shots/.

import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

// require() also finds a globally installed Playwright (NODE_PATH); ESM imports would not.
const { chromium, devices } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE ?? "http://localhost:3000";
const EMAIL = process.env.OWNER_EMAIL ?? "owner@example.com";
const PASSWORD = process.env.OWNER_PASSWORD ?? "Smoke-Test-Pass-42";
const VIDEO = process.env.VIDEO_FILE;
const SHOTS = "e2e-shots";
const PAGES = ["/", "/dashboard", "/videos", "/connections", "/learning", "/settings"];
mkdirSync(SHOTS, { recursive: true });

const problems = [];
const watch = (page) => {
  page.on("pageerror", (e) => problems.push(`page error on ${page.url()}: ${e.message}`));
  page.on("response", (r) => { if (r.status() >= 500) problems.push(`HTTP ${r.status()} ${r.url()}`); });
};

const browser = await chromium.launch();
const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await desktop.newPage();
watch(page);

const status = await (await page.request.get(`${BASE}/api/auth/status`)).json();
if (status.setup_required) {
  await page.goto(`${BASE}/setup`);
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
} else {
  await page.goto(`${BASE}/login`);
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
}
await page.waitForURL((u) => u.pathname === "/", { timeout: 20000 });

for (const path of PAGES) {
  await page.goto(BASE + path);
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${SHOTS}/desktop${path === "/" ? "_chat" : path.replace(/\//g, "_")}.png` });
}

if (VIDEO) {
  await page.goto(BASE + "/");
  await page.setInputFiles('input[type="file"]', VIDEO);
  await page.waitForTimeout(3000);
  await page.fill('textarea[aria-label="Message the Brain"]', "Make 1 vertical short from this video");
  await page.click('button[aria-label="Send"]');
  const deadline = Date.now() + 20 * 60 * 1000;
  let workflow;
  while (Date.now() < deadline) {
    const jobs = await page.evaluate(async () => (await fetch("/api/jobs?limit=20")).json());
    workflow = jobs.find((j) => j.type === "workflow.video");
    if (workflow && ["completed", "failed", "cancelled"].includes(workflow.status)) break;
    await page.waitForTimeout(5000);
  }
  if (workflow?.status !== "completed") problems.push(`video workflow ended as ${workflow?.status ?? "missing"}`);
  else {
    await page.goto(`${BASE}/videos/${workflow.project_id}`);
    await page.waitForLoadState("networkidle");
    const meta = await page.locator("video").first().evaluate((el) => new Promise((resolve) => {
      if (el.readyState >= 1) return resolve({ w: el.videoWidth, h: el.videoHeight, d: el.duration });
      el.addEventListener("loadedmetadata", () => resolve({ w: el.videoWidth, h: el.videoHeight, d: el.duration }));
      setTimeout(() => resolve(null), 15000);
    }));
    if (!meta || !meta.d) problems.push("rendered clip did not load in the player");
    else console.log(`clip plays: ${meta.w}x${meta.h}, ${meta.d.toFixed(1)} s`);
    await page.screenshot({ path: `${SHOTS}/desktop_project.png`, fullPage: true });
  }
}

const phone = await browser.newContext({ ...devices["iPhone 13"] });
await phone.addCookies(await desktop.cookies());
const mobile = await phone.newPage();
watch(mobile);
for (const path of PAGES) {
  await mobile.goto(BASE + path);
  await mobile.waitForLoadState("networkidle");
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (overflow > 1) problems.push(`${path} scrolls sideways on a phone (${overflow}px)`);
  await mobile.screenshot({ path: `${SHOTS}/phone${path === "/" ? "_chat" : path.replace(/\//g, "_")}.png` });
}

await browser.close();
if (problems.length) {
  console.error("FAILED\n" + problems.join("\n"));
  process.exit(1);
}
console.log(`OK — ${PAGES.length} pages on desktop and phone${VIDEO ? ", video processed" : ""}. Screenshots in ${SHOTS}/`);
