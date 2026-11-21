// Browser walk-through of the scheduling, folder, download and one-click-connect features against a
// running CLIPFORGE (Docker stack). Uses the real pipeline on a real video.
//
//   BASE=http://localhost:3000 OWNER_EMAIL=… OWNER_PASSWORD=… INBOX_DIR=./inbox VIDEO_FILE=talk.mp4 node e2e/features.mjs
//
// Screenshots go to ./e2e-shots/features-*.png. Fails on page errors or HTTP 5xx responses.
import { copyFileSync, mkdirSync, utimesSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const { chromium } = createRequire(import.meta.url)("playwright");
const BASE = process.env.BASE ?? "http://localhost:3000";
const EMAIL = process.env.OWNER_EMAIL ?? "owner@example.com";
const PASSWORD = process.env.OWNER_PASSWORD ?? "Smoke-Test-Pass-42";
const INBOX = process.env.INBOX_DIR ?? "inbox";
const VIDEO = process.env.VIDEO_FILE;
const SHOTS = "e2e-shots";
mkdirSync(SHOTS, { recursive: true });

const problems = [];
const results = [];
const check = (ok, label) => { results.push(`${ok ? "PASS" : "FAIL"} ${label}`); console.log(`${ok ? "PASS" : "FAIL"} ${label}`); if (!ok) problems.push(label); };
const shot = async (page, name) => { await page.waitForTimeout(400); /* let entrance animations finish */ await page.screenshot({ path: `${SHOTS}/features-${name}.png`, fullPage: false }); };
// A dialog must be fully on screen and on top: the element at its centre has to belong to it.
const dialogOnTop = (page) => page.evaluate(() => {
  const dialog = document.querySelector('[role="dialog"]');
  if (!dialog) return false;
  const box = dialog.getBoundingClientRect();
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + Math.min(box.height / 2, 120));
  return box.top >= 0 && box.bottom <= window.innerHeight + 1 && dialog.contains(hit);
});

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 950 }, deviceScaleFactor: 1 });
const page = await context.newPage();
page.on("pageerror", (e) => problems.push(`page error on ${page.url()}: ${e.message}`));
page.on("response", (r) => { if (r.status() >= 500) problems.push(`HTTP ${r.status()} ${r.url()}`); });

// ---- sign in
await page.goto(`${BASE}/login`);
await page.fill('input[type="email"]', EMAIL);
await page.fill('input[type="password"]', PASSWORD);
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
const api = async (p, opts = {}) => {
  const csrf = await page.evaluate(() => document.cookie);
  return page.request.fetch(`${BASE}/api${p}`, opts);
};

// ---- 1. diagnostics report the downloader
const diag = await (await api("/diagnostics")).json();
check(diag.downloader?.ok && diag.downloader?.js_runtime === "deno" && diag.downloader?.youtube_challenge_solver,
  `downloader ready in the container (yt-dlp ${diag.downloader?.yt_dlp}, deno, challenge solver)`);

// ---- 2. Settings → Sources
await page.goto(`${BASE}/settings#sources`);
await page.waitForSelector("#sources");
await page.locator("#sources").scrollIntoViewIfNeeded();
check(await page.getByText("Download public YouTube and Instagram videos").isVisible(), "Settings shows the public-downloads switch");
await shot(page, "1-settings-sources");

// ---- 3. One-click connect: guided set-up, credentials checked with the real Google endpoint
await page.goto(`${BASE}/settings`);
await page.evaluate(async () => {  // start from "not set up" so the walk-through can run again
  const csrf = (await (await fetch("/api/auth/me")).json()).csrf_token;
  await fetch("/api/connections/apps/google", { method: "DELETE", headers: { "x-csrf-token": csrf } });
});
await page.goto(`${BASE}/settings#connections`);
await page.locator("#connections").scrollIntoViewIfNeeded();
const connect = page.locator("#connections").getByRole("button", { name: "Connect" }).first();
await connect.click();
const dialog = page.getByRole("dialog");
await dialog.waitFor();
check(await dialog.getByText("Set up Google sign-in (once)").isVisible(), "Connect opens the guided Google set-up when no app is known");
check(await dialogOnTop(page), "the set-up dialog is on top of the page, fully visible");
await shot(page, "2-connect-setup");
await dialog.getByPlaceholder("1234…apps.googleusercontent.com").fill("123456789012-clipforgetestclient.apps.googleusercontent.com");
await dialog.locator('input[type="password"]').fill("GOCSPX-not-a-real-secret-123");
await dialog.getByRole("button", { name: "Save and connect" }).click();
const toast = page.locator('[aria-live="polite"] > div').filter({ hasText: /recognise|reached|Saved/ }).first();
await toast.waitFor({ timeout: 30000 }).catch(() => undefined);
const toastText = (await toast.textContent().catch(() => "")) ?? "";
if (/couldn't be reached/.test(toastText)) {
  console.log(`BLOCKED the credential check could not reach Google from the container ("${toastText.trim().slice(0, 80)}")`);
} else {
  check(/doesn't recognise/.test(toastText), `a made-up client is rejected by Google itself ("${toastText.trim().slice(0, 80)}")`);
}
await shot(page, "3-connect-rejected");
await page.keyboard.press("Escape");

// ---- 4. Watch a folder
await page.goto(`${BASE}/videos`);
await page.getByRole("button", { name: "Process a folder" }).click();
await page.getByRole("radio", { name: /Watch a CLIPFORGE folder/ }).click();
check(await dialogOnTop(page), "the Process a folder dialog is on top of the page, fully visible");
const already = (await (await api("/batches")).json()).some((b) => b.name === "Folder: Podcast");
if (!already) {
  await page.getByRole("button", { name: /Podcast/ }).click();
  await page.getByPlaceholder(/3 Shorts under 45 seconds/).fill("make 2 shorts under 45 seconds about pricing");
  await page.getByRole("radio", { name: /Ask me first/ }).click();
  await shot(page, "4-process-folder-dialog");
  await page.getByRole("button", { name: "Start watching" }).click();
} else {
  check(await page.getByText("already watched").isVisible(), "a watched folder can't be added twice");
  await shot(page, "4-process-folder-dialog");
  await page.keyboard.press("Escape");
}
await page.getByText("Folder: Podcast").waitFor({ timeout: 15000 });
check(true, "watching the Podcast folder");

const existing = await (async () => {
  const batch = (await (await api("/batches")).json()).find((b) => b.name === "Folder: Podcast");
  return batch ? (await (await api(`/projects?batch_id=${batch.id}`)).json()) : [];
})();
if (VIDEO && !existing.length) {
  const target = path.join(INBOX, "Podcast", "Pricing talk.mp4");
  copyFileSync(VIDEO, target);
  const old = new Date(Date.now() - 10 * 60 * 1000);
  utimesSync(target, old, old);
  // The folder is checked every minute; the video then runs through the real pipeline.
  let projects = [];
  const deadline = Date.now() + 25 * 60 * 1000;
  while (Date.now() < deadline) {
    const batches = await (await api("/batches")).json();
    const batch = batches.find((b) => b.name === "Folder: Podcast");
    if (batch) {
      projects = await (await api(`/projects?batch_id=${batch.id}`)).json();
      if (projects.length && ["awaiting_approval", "ready", "failed", "completed", "access_required"].includes(projects[0].status)) break;
    }
    await page.waitForTimeout(10000);
  }
  check(projects.length === 1, `the video put in the folder became a project (${projects[0]?.status_label ?? "none"})`);
  await page.goto(`${BASE}/videos`);
  await page.waitForTimeout(1500);
  await shot(page, "5-batch-progress");
}
{
  const batch = (await (await api("/batches")).json()).find((b) => b.name === "Folder: Podcast");
  const projects = batch ? await (await api(`/projects?batch_id=${batch.id}`)).json() : [];
  if (projects[0]) {
    await page.goto(`${BASE}/videos/${projects[0].id}`);
    await page.waitForTimeout(2500);
    await shot(page, "6-project-approval");
    // ---- 5. schedule one clip for tomorrow 18:00 from its package
    const schedule = page.getByRole("button", { name: "Schedule…" }).first();
    if (await schedule.count()) {
      await schedule.click();
      const modal = page.getByRole("dialog");
      await modal.getByRole("button", { name: "Tomorrow 18:00" }).click();
      check(await dialogOnTop(page), "the schedule dialog is on top of the page, fully visible");
      await shot(page, "7-schedule-dialog");
      await modal.getByRole("button", { name: "Schedule", exact: true }).click();
      await page.getByText(/Goes live/).first().waitFor({ timeout: 15000 });
      check(true, "a package was scheduled for tomorrow 18:00");
      await shot(page, "8-package-scheduled");
      await page.goto(`${BASE}/dashboard`);
      await page.getByText("Upcoming posts").waitFor({ timeout: 15000 });
      await page.getByText("Upcoming posts").scrollIntoViewIfNeeded();
      check(await page.getByText("CLIPFORGE posts it").first().isVisible(), "Dashboard lists the upcoming post and how it goes out");
      await shot(page, "9-dashboard-upcoming");
    } else {
      check(await page.getByText(/Goes live/).first().isVisible(), "the project's packages are already scheduled");
    }
  }
  const working = await page.goto(`${BASE}/dashboard`).then(() => page.locator("text=Working on now").locator("..").locator("..").innerText());
  check(!/maintenance|publish_scheduled/.test(working), "Working on now lists no housekeeping jobs");
}

// ---- 6. chat understands a publish time
await page.goto(`${BASE}/`);
await page.getByRole("textbox").first().fill("publish clip 1 tomorrow at 6pm");
await page.keyboard.press("Enter");
await page.getByText(/Scheduled \d+ post|nothing waiting|don't have finished clips|couldn't schedule/i).first().waitFor({ timeout: 60000 });
await shot(page, "10-chat-schedule");
check(true, "chat answered a scheduling request");

await browser.close();
for (const p of problems) console.log("PROBLEM:", p);
process.exit(problems.length ? 1 : 0);
