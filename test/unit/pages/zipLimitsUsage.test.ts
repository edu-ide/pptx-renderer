import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pagesDir = resolve(__dirname, '../../pages');

function readPage(name: string): string {
  return readFileSync(resolve(pagesDir, name), 'utf-8');
}

describe('test pages ZIP limits', () => {
  it('uses recommended ZIP limits on pages that parse untrusted PPTX input', () => {
    for (const page of ['index.html', 'render-slide.html', 'export.html', 'e2e-compare.html']) {
      expect(readPage(page), `${page} should use RECOMMENDED_ZIP_LIMITS`).toContain(
        'RECOMMENDED_ZIP_LIMITS',
      );
    }
  });
});
