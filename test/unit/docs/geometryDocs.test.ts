import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { ooxmlPresetRuntimeShapeNames } from '../../../src/shapes/ooxmlGeometryRuntime';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');

function readDoc(path: string): string {
  return readFileSync(resolve(root, path), 'utf8');
}

describe('OOXML geometry documentation contract', () => {
  it('keeps the production subset count and bounded donut contract aligned', () => {
    expect(ooxmlPresetRuntimeShapeNames).toHaveLength(29);
    expect(ooxmlPresetRuntimeShapeNames).toContain('donut');

    for (const path of [
      'README.md',
      'docs/ARCHITECTURE.md',
      'docs/TESTING.md',
      'scripts/ooxml-geometry/README.md',
    ]) {
      const contents = readDoc(path);
      expect(contents, path).toMatch(/29(?:-definition| definitions)/);
      expect(contents, path).toContain('donut');
      expect(contents, path).toContain('25000');
      expect(contents, path).toContain('50000');
    }
  });
});
