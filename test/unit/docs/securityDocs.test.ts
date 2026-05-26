import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, '../../..');

function readDoc(path: string): string {
  return readFileSync(resolve(root, path), 'utf-8');
}

describe('security documentation examples', () => {
  it('does not show untrusted parseZip examples without recommended limits', () => {
    const readme = readDoc('README.md');

    expect(readme).not.toContain('parseZip(arrayBuffer);');
  });

  it('defines imports for examples that use recommended ZIP limits', () => {
    const performanceGuide = readDoc('docs/PERFORMANCE.md');

    expect(performanceGuide).toContain(
      "import { PptxViewer, RECOMMENDED_ZIP_LIMITS } from '@aiden0z/pptx-renderer';",
    );
  });

  it('documents decoded-entry ZIP limit fallback behavior', () => {
    const readme = readDoc('README.md');
    const securityGuide = readDoc('docs/SECURITY.md');

    expect(readme).toContain('actual decoded entry size');
    expect(securityGuide).toContain('actual decoded entry size');
  });

  it('documents render request supersession semantics', () => {
    const readme = readDoc('README.md');
    const architectureGuide = readDoc('docs/ARCHITECTURE.md');

    expect(readme).toContain('newer render request supersedes older queued or batched work');
    expect(architectureGuide).toContain('newer render request supersedes older queued or batched work');
  });
});
