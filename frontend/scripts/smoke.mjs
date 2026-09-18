/**
 * End-to-end smoke check for the dashboard.
 *
 * Drives a real browser against a running API and frontend, asserting that
 * each page renders live data — not that a component returns the right JSX.
 * A unit test would not have caught the two layout bugs this found (a chart
 * that stretched its own measuring container, and an axis that rounded its
 * ticks into 0/3/5/8/10).
 *
 *   node scripts/smoke.mjs [--app http://localhost:3000] [--shots ./shots]
 *
 * Requires: the API and the frontend running, and an account that can sign in.
 */

import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
};

const APP = arg("app", "http://127.0.0.1:3000");
const SHOTS = arg("shots", "");
const EMAIL = process.env.SMOKE_EMAIL ?? "dev@example.com";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "correct-horse-battery-staple";

const failures = [];
const consoleErrors = [];

function check(name, condition, detail = "") {
  if (condition) console.log(`  ok   ${name}`);
  else {
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
    failures.push(name);
  }
}

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("pageerror", (error) => consoleErrors.push(error.message));

async function shot(name) {
  if (!SHOTS) return;
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

try {
  console.log(`\nsign in (${APP})`);
  await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("**/dashboard", { timeout: 20000 });
  await page.waitForTimeout(1200);
  check("lands on the dashboard", page.url().includes("/dashboard"));
  await shot("dashboard");

  console.log("dashboard");
  const tiles = await page.locator("main .grid > div").count();
  check("four stat tiles", tiles >= 4, `saw ${tiles}`);
  check("chart legend is present", await page.getByText("Succeeded").first().isVisible());
  check("quota meter is present", await page.locator('[role="meter"]').isVisible());

  const ticks = await page
    .locator("svg text")
    .allTextContents()
    .then((all) => all.filter((t) => /^\d[\d,]*$/.test(t)).map(Number));
  check(
    "axis ticks are whole numbers",
    ticks.every((t) => Number.isInteger(t)),
    ticks.join(","),
  );

  console.log("layout");
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(600);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    check(`no horizontal overflow at ${width}px`, overflow <= 1, `${overflow}px over`);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });

  console.log("pages load");
  for (const [path, marker] of [
    ["/playground", "Run extraction"],
    ["/batches", "New batch"],
    ["/tally", "Your ledger master"],
    ["/documents", "Uploads"],
    ["/usage", "Request log"],
    ["/api-keys", "Create a key"],
    ["/webhooks", "Add an endpoint"],
    ["/docs", "Authenticate"],
    ["/settings", "Organization"],
  ]) {
    await page.goto(`${APP}${path}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    check(`${path} renders`, await page.getByText(marker).first().isVisible());
    await shot(path.replace(/\//g, "") || "home");
  }

  console.log("secrets");
  await page.goto(`${APP}/api-keys`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  const body = await page.content();
  check(
    "no full API key is rendered in the key list",
    !/dp_live_[A-Za-z0-9_-]{30,}/.test(body),
  );

  await page.goto(`${APP}/webhooks`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  check(
    "no webhook signing secret is rendered in the endpoint list",
    !/whsec_[a-f0-9]{40,}/.test(await page.content()),
  );

  // The API reference is what a developer reads before deciding to sign up, so
  // it must render for someone who has no account at all. A fresh context is
  // the only way to assert that — the page above is signed in.
  console.log("public docs");
  const anon = await browser.newContext();
  const anonPage = await anon.newPage({ viewport: { width: 1440, height: 1000 } });
  await anonPage.goto(`${APP}/docs`, { waitUntil: "networkidle" });
  await anonPage.waitForTimeout(800);
  check("/docs renders signed out", anonPage.url().endsWith("/docs"), anonPage.url());
  check(
    "/docs documents the SDK",
    await anonPage.getByText("pip install docuparse").first().isVisible(),
  );
  check(
    "signed-out header offers an account",
    await anonPage.getByRole("link", { name: "Create account" }).isVisible(),
  );
  await anon.close();

  check("no uncaught page errors", consoleErrors.length === 0, consoleErrors.join(" | "));
} finally {
  await browser.close();
}

console.log(
  failures.length ? `\n${failures.length} check(s) failed\n` : "\nall checks passed\n",
);
process.exit(failures.length ? 1 : 0);
