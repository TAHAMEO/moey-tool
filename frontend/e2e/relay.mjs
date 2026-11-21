// Checks deploy/oauth-relay/instagram.html in a real browser: it forwards Instagram's sign-in result
// only to CLIPFORGE on the same computer, and refuses anything else.
//   node frontend/e2e/relay.mjs
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const here = path.dirname(fileURLToPath(import.meta.url));
const relay = pathToFileURL(path.resolve(here, "../../deploy/oauth-relay/instagram.html")).href;

const received = [];
const server = createServer((req, res) => { received.push(req.url); res.end("ok"); });
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;

const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
let failed = 0;
const check = (ok, label) => { console.log(`${ok ? "PASS" : "FAIL"} ${label}`); if (!ok) failed += 1; };

// 1. A real sign-in result is forwarded to 127.0.0.1:<port> with code and state only.
await page.goto(`${relay}?code=AQ-code&state=instagram.nonce123.i${port}&extra=drop-me#_`);
await page.waitForURL(`http://127.0.0.1:${port}/**`, { timeout: 5000 }).catch(() => undefined);
const forwarded = received.at(-1) ?? "";
check(forwarded.startsWith("/api/connections/instagram/callback?"), "forwards to the CLIPFORGE callback on this computer");
const params = new URLSearchParams(forwarded.split("?")[1] ?? "");
check(params.get("code") === "AQ-code" && params.get("state") === `instagram.nonce123.i${port}`, "passes code and state unchanged");
check(!params.has("extra"), "drops anything else");

// 2. A user-denied sign-in is forwarded too (so CLIPFORGE can say so).
await page.goto(`${relay}?error=access_denied&error_reason=user_denied&state=instagram.n2.i${port}`);
await page.waitForURL(`http://127.0.0.1:${port}/**`, { timeout: 5000 }).catch(() => undefined);
check((received.at(-1) ?? "").includes("error=access_denied"), "forwards a cancelled sign-in");

// 3. Anything that isn't a CLIPFORGE state never leaves the page.
const before = received.length;
for (const state of ["instagram.n3.evil.com", "instagram.n3.x99", "other.n3.l80", "instagram.n3.l99999", "instagram.n3"]) {
  await page.goto(`${relay}?code=stolen&state=${encodeURIComponent(state)}`);
  await page.waitForTimeout(300);
  const text = await page.textContent("#msg");
  check(page.url().startsWith("file:") && /wasn't forwarded|didn't come from/.test(text ?? ""), `refuses state ${state}`);
}
check(received.length === before, "no request was made for refused states");
check(errors.length === 0, `no script/CSP errors (${errors.join("; ")})`);

await browser.close();
server.close();
console.log(failed ? `${failed} check(s) failed` : "relay page OK");
process.exit(failed ? 1 : 0);
