import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const exportPagePath = resolve(__dirname, '../../pages/export.html');
const html = readFileSync(exportPagePath, 'utf-8');

describe('export page security', () => {
  it('does not interpolate table cell text into innerHTML', () => {
    expect(html).not.toContain('${cell.text');
    expect(html).toContain('cellText.textContent');
  });
});
