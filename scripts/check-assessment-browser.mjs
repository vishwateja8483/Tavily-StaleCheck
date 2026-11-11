import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [driver, python, outputArg, installed] = process.argv.slice(2);
const output = resolve(outputArg);
await mkdir(output, { recursive: false });
const server = spawn(python, [...(installed === '--installed' ? ['-I'] : []), '-m', 'stalecheck.cli', 'serve-assessment', '--output', resolve(output, 'sessions'), '--port', '0'], {
  env: { ...process.env, PYTHONPATH: resolve('src') }, stdio: ['ignore', 'pipe', 'pipe'],
});
let browser;
try {
  const launch = await new Promise((accept, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(new Error('Local service launch timed out')), 15000);
    server.stdout.on('data', chunk => {
      text += chunk;
      const match = text.match(/http:\/\/127\.0\.0\.1:\d+\/#token=[A-Za-z0-9_-]+/);
      if (match) { clearTimeout(timer); accept(match[0]); }
    });
    server.on('exit', () => { clearTimeout(timer); reject(new Error('Local service exited')); });
  });
  const origin = new URL(launch).origin;
  const { chromium } = await import(pathToFileURL(resolve(driver)).href);
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || undefined, headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const errors = [], external = [], ids = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', async response => {
    if (response.url() === origin + '/api/jobs' && response.status() === 201) ids.push((await response.json()).id);
  });
  await page.route(/^https?:/, route => {
    if (new URL(route.request().url()).origin !== origin) { external.push('external request blocked'); return route.abort(); }
    return route.continue();
  });
  await page.goto(launch);
  await page.getByRole('heading', { name: 'Demo mode — mocked providers' }).waitFor();
  if (new URL(page.url()).hash) throw new Error('Launch fragment not stripped');
  if (await page.evaluate(() => localStorage.length + sessionStorage.length)) throw new Error('Unexpected persistent storage');
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'setup-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile setup overflow');
  await page.setViewportSize({ width: 1440, height: 1050 });
  const start = async () => {
    await page.getByRole('button', { name: 'Load Acme example' }).click();
    for (const label of ['Full answer', 'Verification date', 'Answer date (optional)', 'Primary domains']) {
      const field = page.getByLabel(label, { exact: label !== 'Primary domains' });
      if (!await field.evaluate(element => element.readOnly)) throw new Error('Demo fixture field is editable');
    }
    if (await page.getByRole('checkbox').isChecked()) throw new Error('Demo load must require fresh consent');
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Start mocked assessment' }).click();
    const approval = page.getByRole('heading', { name: '2. Review exact search request' });
    await approval.waitFor();
    if (!await approval.evaluate(el => el === document.activeElement)) throw new Error('Approval did not receive focus');
    await page.getByText('"Acme Cloud Starter plan price per month"', { exact: true }).waitFor();
  };
  await start();
  await page.getByRole('button', { name: 'Decline searches' }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('status').filter({ hasText: 'declined' }).waitFor();
  await page.getByRole('button', { name: 'Prepare another assessment' }).click();
  if (!await page.getByRole('heading', { name: 'Answer and scope' }).evaluate(el => el === document.activeElement)) throw new Error('New assessment did not return focus to setup');
  await start();
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'approval-desktop.png'), fullPage: true });
  await page.locator('.assessment-progress').screenshot({ animations: 'disabled', path: resolve(output, 'approval-detail.png') });
  await page.getByRole('button', { name: 'Approve exact mocked searches' }).click();
  await page.getByRole('status').filter({ hasText: 'completed' }).waitFor();
  await page.getByRole('button', { name: 'Open verified report' }).click();
  const reportHeading = page.getByRole('heading', { name: 'Server-verified report' });
  await reportHeading.waitFor();
  if (!await reportHeading.evaluate(el => el === document.activeElement)) throw new Error('Opening the report did not focus its heading');
  const frame = page.frameLocator('iframe');
  await frame.getByText('Full original answer', { exact: true }).waitFor();
  await frame.getByRole('heading', { name: /Potentially changed/ }).waitFor();
  await frame.getByRole('heading', { name: 'At a glance' }).waitFor();
  await frame.locator('section').filter({ hasText: 'At a glance' }).screenshot({ animations: 'disabled', path: resolve(output, 'report-summary.png') });
  const verdicts = await frame.locator('article').evaluateAll(articles => articles.map(article => article.dataset.verdict));
  if (verdicts.join(',') !== 'POTENTIALLY_CHANGED,MODEL_SUPPORTED,UNVERIFIABLE,UNVERIFIABLE') throw new Error('The demo did not show all three verdicts');
  if (await frame.locator('body').evaluate(el => el.ownerDocument.compatMode) !== 'CSS1Compat') throw new Error('Report is not in standards mode');
  await frame.getByRole('link', { name: 'claim-1', exact: true }).click();
  await frame.locator('article:target').waitFor({ timeout: 2000 });
  await frame.locator('main').evaluate(el => el.ownerDocument.defaultView.scrollTo(0, 0));
  await frame.getByText(/Category \(model-labelled\): pricing\. Freshness of the quoted evidence: current \(score \d+\/100/).waitFor();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download report.json', exact: true }).click();
  await (await downloaded).saveAs(resolve(output, 'downloaded-report.json'));
  await page.locator('iframe').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'report-desktop-viewport.png') });
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'result-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('iframe').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'report-mobile-viewport.png') });
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'result-mobile.png'), fullPage: true });
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile page overflow');
  if (await frame.locator('body').evaluate(el => el.scrollWidth > el.ownerDocument.documentElement.clientWidth)) throw new Error('Mobile report overflow');
  await page.getByRole('button', { name: 'Close report' }).click();
  if (!await page.getByRole('button', { name: 'Open verified report' }).evaluate(el => el === document.activeElement)) throw new Error('Closing the report did not restore focus');
  await page.getByRole('button', { name: 'Prepare another assessment' }).click();
  await start();
  await page.getByRole('button', { name: 'Cancel assessment', exact: true }).click();
  await page.getByRole('status').filter({ hasText: 'cancelled' }).waitFor();
  await page.reload();
  await page.getByRole('alert').filter({ hasText: 'Launch token missing' }).waitFor();
  if (errors.length || external.length) throw new Error(JSON.stringify({ errors, external }));
  await writeFile(resolve(output, 'browser-check.json'), JSON.stringify({ status: 'passed', mode: 'demo', live_calls: false,
    checks: ['fixed demo fixture', 'fresh demo consent', 'fragment stripped', 'no persistent storage', 'exact approval gate', 'workflow focus', 'keyboard decline', 'demo completion', 'sandbox report rendered', 'per-claim freshness score shown', 'at-a-glance table shown', 'claim jump link', 'authenticated download', 'mobile overflow', 'cancel', 'reload loses token'],
    errors, external, job_ids: ids }, null, 2), { flag: 'wx' });
  console.log('Assessment browser journey passed (demo only).');
} finally {
  await browser?.close();
  server.kill();
}
