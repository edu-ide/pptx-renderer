import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const indexHtml = readFileSync(resolve(__dirname, '../../pages/index.html'), 'utf-8');
const renderSlideHtml = readFileSync(resolve(__dirname, '../../pages/render-slide.html'), 'utf-8');

describe('test page error rendering security', () => {
  it('basic preview page does not interpolate render errors into innerHTML', () => {
    expect(indexHtml).not.toContain('<small>${e.message || e}</small>');
    expect(indexHtml).toContain('renderEmptyStateError(e)');
  });

  it('single slide page does not interpolate load errors into innerHTML', () => {
    expect(renderSlideHtml).not.toContain('${e.message}</div>');
    expect(renderSlideHtml).toContain('renderLoadError(e)');
  });
});
