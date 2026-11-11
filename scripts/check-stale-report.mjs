// Drive a local static report with an already installed browser; never download.
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { writeFile } from 'node:fs/promises';
const [driver, html, output] = process.argv.slice(2);
const { chromium } = await import(pathToFileURL(resolve(driver)).href);
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || undefined, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://**/*', route => route.abort());
  await page.route('https://**/*', route => route.abort());
  await page.goto(pathToFileURL(resolve(html)).href);
  await page.getByRole('heading', { name: 'Full original answer', exact: true }).waitFor();
  const articles = await page.locator('article').count();
  if (articles !== 4 || await page.locator('script').count() || errors.length) throw new Error('Invalid report rendering');
  await page.screenshot({ path: output + '.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  if (overflow) throw new Error('Mobile horizontal overflow');
  await writeFile(output + '.json', JSON.stringify({ status: 'passed', articles, scriptElements: 0, mobileHorizontalOverflow: false, errors }, null, 2), { flag: 'wx' });
  console.log('Static report browser checks passed');
} finally {
  await browser.close();
}
