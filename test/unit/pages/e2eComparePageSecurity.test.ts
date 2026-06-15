import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pagePath = resolve(__dirname, '../../pages/e2e-compare.html');
const html = readFileSync(pagePath, 'utf-8');

describe('e2e compare page security', () => {
  it('does not render log messages through innerHTML', () => {
    expect(html).not.toContain('<span>${msg}</span>');
    expect(html).toContain('message.textContent = String(msg)');
  });

  it('does not interpolate caught error messages into the main panel HTML', () => {
    expect(html).not.toContain('<p>${e.message}</p>');
    expect(html).toContain('renderMainError(e)');
  });

  it('does not interpolate full-evaluation labels or errors into table HTML', () => {
    expect(html).not.toContain('<td>${r.label}</td><td colspan="10"');
    expect(html).not.toContain('<td>${r.label}</td>');
    expect(html).toContain('appendTextCell(errorRow, r.label)');
    expect(html).toContain('appendTextCell(row, r.label)');
  });

  it('encodes test file names before placing them in API or testdata URLs', () => {
    expect(html).toContain('encodeURIComponent(testFile)');
    expect(html).toContain('encodeURIComponent(name)');
    expect(html).toContain('encodeURIComponent(r.testFile)');
  });
});
