// Drives the packaged assessment page against a live-configured local service (real Bifrost, real model, real Tavily).
// usage: node scripts/check-assessment-live.mjs <playwright index.mjs> <python> <new output dir> <answer file> <domain> [max claims]
// The server reads BIFROST_URL, BIFROST_MODEL, BIFROST_VIRTUAL_KEY and TAVILY_API_KEY (or BIFROST_SEARCH_RELAY) from this process's environment.
import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [driver, python, outputArg, answerFile, domain, claims = '3'] = process.argv.slice(2);
const output = resolve(outputArg);
await mkdir(output, { recursive: false });
const answer = (await readFile(answerFile, 'utf8')).trim();
const server = spawn(python, ['-m', 'stalecheck.cli', 'serve-assessment', '--live', '--output', resolve(output, 'sessions'), '--port', '0'], {
  env: { ...process.env, PYTHONPATH: resolve('src') }, stdio: ['ignore', 'pipe', 'pipe'],
});
let browser;
try {
  const launch = await new Promise((accept, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(new Error('Local service launch timed out')), 15000);
    server.stdout.on('data', chunk => { text += chunk; const match = text.match(/http:\/\/127\.0\.0\.1:\d+\/#token=[A-Za-z0-9_-]+/); if (match) { clearTimeout(timer); accept(match[0]); } });
    server.on('exit', () => { clearTimeout(timer); reject(new Error('Local service exited')); });
  });
  const origin = new URL(launch).origin;
  const { chromium } = await import(pathToFileURL(resolve(driver)).href);
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || undefined, headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [], unexpectedRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(/^https?:/, route => {
    const target = new URL(route.request().url()).origin;
    if (target === origin) return route.continue();
    unexpectedRequests.push(target);
    return route.abort();
  });
  await page.goto(launch);
  await page.getByRole('heading', { name: 'Bifrost and Tavily' }).waitFor();
  const configuration = await page.locator('.configuration li').evaluateAll(items => items.map(item => Array.from(item.children, child => child.textContent).join(' — ')));
  await page.getByRole('textbox', { name: 'Full answer' }).fill(answer);
  await page.getByRole('textbox', { name: /^Primary domains/ }).fill(domain);
  await page.getByText('Run limits', { exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Maximum claims' }).fill(claims);
  await page.getByRole('spinbutton', { name: 'Maximum attempts' }).fill(String(1 + 2 * Number(claims)));
  await page.getByText('Run limits', { exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.screenshot({ path: resolve(output, '1-live-form.png'), clip: { x: 0, y: 0, width: 1280, height: 900 } });
  const started = Date.now();
  await page.getByRole('button', { name: 'Start live assessment' }).click();
  const approval = page.getByRole('heading', { name: '2. Review exact search request' });
  const terminal = page.locator('[role="status"]:is([data-state="completed"], [data-state="partial"], [data-state="failed"], [data-state="expired"])');
  await approval.or(terminal).first().waitFor({ timeout: 150000 });
  if (!await approval.isVisible()) throw new Error(`Assessment ended before approval: ${await terminal.innerText()}`);
  const queries = await page.locator('.assessment-approval ol li').allInnerTexts();
  await page.locator('.assessment-approval').scrollIntoViewIfNeeded();
  await page.screenshot({ animations: 'disabled', path: resolve(output, '2-live-approval.png') });
  await page.getByRole('button', { name: 'Approve exact searches' }).click();
  await terminal.waitFor({ timeout: 175000 });
  const state = await terminal.getAttribute('data-state');
  const seconds = Math.round((Date.now() - started) / 1000);
  await page.getByRole('button', { name: 'Open verified report' }).click();
  const frame = page.frameLocator('iframe');
  await frame.getByText('Full original answer', { exact: true }).waitFor();
  const headings = await frame.locator('article h2').allInnerTexts();
  const glance = await frame.locator('table').first().innerText();
  await page.locator('iframe').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.screenshot({ path: resolve(output, '3-live-report.png') });
  const archiveButton = page.getByRole('button', { name: 'Download verified run (.zip)', exact: true });
  await archiveButton.scrollIntoViewIfNeeded();
  const archive = page.waitForEvent('download');
  await archiveButton.click();
  await (await archive).saveAs(resolve(output, 'verified-run.zip'));
  const storage = await page.evaluate(() => localStorage.length + sessionStorage.length);
  const summary = { launchFragmentStripped: !new URL(page.url()).hash, storage, pageErrors: errors, unexpectedRequests, configuration, queries, state, seconds, claimHeadings: headings, glance };
  await writeFile(resolve(output, 'summary.json'), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
} finally {
  if (browser) await browser.close();
  server.kill();
}
