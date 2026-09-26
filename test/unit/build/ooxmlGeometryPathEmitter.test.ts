import { describe, expect, it } from 'vitest';

import {
  compilePresetShapeDefinitions,
  evaluatePresetShape,
} from '../../../scripts/ooxml-geometry/geometry-ir.mjs';
import {
  emitPresetShapePaths,
  summarizeEmittedPresetShape,
} from '../../../scripts/ooxml-geometry/path-emitter.mjs';
import {
  buildPathEmissionContract,
  buildRuntimeGeometryModule,
} from '../../../scripts/ooxml-geometry/generate.mjs';
import { loadPinnedPresetShapeDefinitions } from '../../../scripts/ooxml-geometry/source-validator.mjs';
import sourceManifest from '../../../scripts/ooxml-geometry/source-manifest.json';
import sourceReconciliation from '../../../scripts/ooxml-geometry/source-reconciliation.json';

const repositoryRoot = process.cwd();

function evaluatedShape(
  commands: unknown[],
  {
    name = 'testShape',
    width = 200,
    height = 100,
    pathWidth = 100,
    pathHeight = 50,
    fill = 'norm',
    stroke = true,
    extrusionOk = true,
  } = {},
) {
  return {
    name,
    width,
    height,
    adjustments: {},
    guideValues: {},
    adjustHandles: [],
    connectionSites: [],
    textRectangle: null,
    paths: [
      {
        width: pathWidth,
        height: pathHeight,
        fill,
        stroke,
        extrusionOk,
        commands,
      },
    ],
  };
}

describe('OOXML evaluated-path SVG emitter', () => {
  it('scales and emits move, line, quadratic, cubic, and close commands', () => {
    const emitted = emitPresetShapePaths(
      evaluatedShape([
        { type: 'moveTo', x: 1, y: 2 },
        { type: 'lnTo', x: 3, y: 4 },
        { type: 'quadBezTo', control: { x: 5, y: 6 }, end: { x: 7, y: 8 } },
        {
          type: 'cubicBezTo',
          control1: { x: 9, y: 10 },
          control2: { x: 11, y: 12 },
          end: { x: 13, y: 14 },
        },
        { type: 'close' },
      ]),
    );

    expect(emitted).toEqual({
      name: 'testShape',
      width: 200,
      height: 100,
      paths: [
        {
          d: 'M2,4 L6,8 Q10,12 14,16 C18,20 22,24 26,28 Z',
          fill: 'norm',
          stroke: true,
          extrusionOk: true,
        },
      ],
    });
  });

  it('rounds only while serializing and canonicalizes negative zero', () => {
    const emitted = emitPresetShapePaths(
      evaluatedShape(
        [
          { type: 'moveTo', x: -0, y: 0 },
          { type: 'lnTo', x: 1 / 3, y: 2 / 3 },
        ],
        { width: 100, height: 50 },
      ),
    );

    expect(emitted.paths[0].d).toBe('M0,0 L0.333333,0.666667');
    expect(emitted.paths[0].d).not.toContain('-0');
  });

  it('converts DrawingML visual angles on a non-circular ellipse', () => {
    const start = {
      x: 200 + 200 / Math.sqrt(5),
      y: 100 + 200 / Math.sqrt(5),
    };
    const emitted = emitPresetShapePaths(
      evaluatedShape(
        [
          { type: 'moveTo', ...start },
          {
            type: 'arcTo',
            widthRadius: 200,
            heightRadius: 100,
            startAngle: 45 * 60000,
            sweepAngle: 90 * 60000,
          },
        ],
        { width: 400, height: 200, pathWidth: 400, pathHeight: 200 },
      ),
    );

    expect(emitted.paths[0].d).toBe('M289.442719,189.442719 A200,100 0 0,1 110.557281,189.442719');
  });

  it('computes arcs in path space before scaling into the target extent', () => {
    const emitted = emitPresetShapePaths(
      evaluatedShape(
        [
          { type: 'moveTo', x: 400, y: 100 },
          {
            type: 'arcTo',
            widthRadius: 200,
            heightRadius: 100,
            startAngle: 0,
            sweepAngle: 180 * 60000,
          },
        ],
        { width: 200, height: 100, pathWidth: 400, pathHeight: 200 },
      ),
    );

    expect(emitted.paths[0].d).toBe('M200,50 A100,50 0 0,1 0,50');
  });

  it('preserves negative sweep direction', () => {
    const emitted = emitPresetShapePaths(
      evaluatedShape(
        [
          { type: 'moveTo', x: 100, y: 50 },
          {
            type: 'arcTo',
            widthRadius: 50,
            heightRadius: 50,
            startAngle: 0,
            sweepAngle: -90 * 60000,
          },
        ],
        { width: 100, height: 100, pathWidth: 100, pathHeight: 100 },
      ),
    );

    expect(emitted.paths[0].d).toBe('M100,50 A50,50 0 0,0 50,0');
  });

  it('splits a full circle so SVG does not collapse equal endpoints', () => {
    const emitted = emitPresetShapePaths(
      evaluatedShape(
        [
          { type: 'moveTo', x: 100, y: 50 },
          {
            type: 'arcTo',
            widthRadius: 50,
            heightRadius: 50,
            startAngle: 0,
            sweepAngle: 360 * 60000,
          },
        ],
        { width: 100, height: 100, pathWidth: 100, pathHeight: 100 },
      ),
    );

    expect(emitted.paths[0].d).toBe('M100,50 A50,50 0 0,1 0,50 A50,50 0 0,1 100,50');
  });

  it('skips zero-radius and zero-sweep arcs without moving the cursor', () => {
    const emitted = emitPresetShapePaths(
      evaluatedShape([
        { type: 'moveTo', x: 1, y: 2 },
        {
          type: 'arcTo',
          widthRadius: 0,
          heightRadius: 5,
          startAngle: 0,
          sweepAngle: 90 * 60000,
        },
        {
          type: 'arcTo',
          widthRadius: 5,
          heightRadius: 5,
          startAngle: 0,
          sweepAngle: 0,
        },
        { type: 'lnTo', x: 3, y: 4 },
      ]),
    );

    expect(emitted.paths[0].d).toBe('M2,4 L6,8');
  });

  it('preserves renderer-owned per-path styling metadata', () => {
    const emitted = emitPresetShapePaths(
      evaluatedShape([{ type: 'moveTo', x: 0, y: 0 }], {
        fill: 'darkenLess',
        stroke: false,
        extrusionOk: false,
      }),
    );

    expect(emitted.paths[0]).toMatchObject({
      fill: 'darkenLess',
      stroke: false,
      extrusionOk: false,
    });
  });

  it('normalizes a sub-pixel negative arc radius caused by floating-point residue', () => {
    const emitted = emitPresetShapePaths(
      evaluatedShape([
        { type: 'moveTo', x: 0, y: 0 },
        {
          type: 'arcTo',
          widthRadius: -Number.EPSILON * 16,
          heightRadius: 5,
          startAngle: 0,
          sweepAngle: 90 * 60000,
        },
      ]),
    );

    expect(emitted.paths[0].d).toBe('M0,0');
  });

  it.each([
    [
      'an arc before a current point',
      evaluatedShape([
        {
          type: 'arcTo',
          widthRadius: 5,
          heightRadius: 5,
          startAngle: 0,
          sweepAngle: 90 * 60000,
        },
      ]),
      /testShape path 0 command 0.*current point/i,
    ],
    [
      'a negative radius',
      evaluatedShape([
        { type: 'moveTo', x: 0, y: 0 },
        {
          type: 'arcTo',
          widthRadius: -1,
          heightRadius: 5,
          startAngle: 0,
          sweepAngle: 90 * 60000,
        },
      ]),
      /testShape path 0 command 1.*widthRadius.*non-negative/i,
    ],
    [
      'a non-finite point',
      evaluatedShape([{ type: 'moveTo', x: Number.NaN, y: 0 }]),
      /testShape path 0 command 0 x.*finite/i,
    ],
    [
      'an unknown command',
      evaluatedShape([{ type: 'mystery' }]),
      /testShape path 0 command 0.*unsupported.*mystery/i,
    ],
    [
      'an invalid path extent',
      evaluatedShape([{ type: 'moveTo', x: 0, y: 0 }], { pathWidth: 0 }),
      /testShape path 0 width.*greater than zero/i,
    ],
  ])('rejects %s with shape and path context', (_description, shape, expected) => {
    expect(() => emitPresetShapePaths(shape)).toThrow(expected as RegExp);
  });

  it('emits every pinned shape at square, wide, and tall extents', async () => {
    const pinned = await loadPinnedPresetShapeDefinitions(repositoryRoot, sourceManifest);
    const ir = compilePresetShapeDefinitions(pinned.xml, sourceManifest, sourceReconciliation);

    for (const dimensions of [
      { width: 216, height: 216 },
      { width: 400, height: 180 },
      { width: 180, height: 400 },
    ]) {
      const failures: string[] = [];
      let emittedPaths = 0;
      for (const shape of ir.shapes) {
        try {
          const emitted = emitPresetShapePaths(evaluatePresetShape(shape, dimensions));
          const summary = summarizeEmittedPresetShape(emitted);
          emittedPaths += summary.paths;
          expect(summary.nonFiniteTokens).toBe(0);
          expect(emitted.paths.every(({ d }: { d: string }) => !/NaN|Infinity/.test(d))).toBe(true);
        } catch (error) {
          failures.push(`${shape.name}: ${(error as Error).message}`);
        }
      }
      expect(failures, `${dimensions.width}x${dimensions.height} emission failures`).toEqual([]);
      expect(emittedPaths).toBe(319);
    }
  }, 20_000);

  it('builds a deterministic all-corpus emission fingerprint for every profile', async () => {
    const pinned = await loadPinnedPresetShapeDefinitions(repositoryRoot, sourceManifest);
    const ir = compilePresetShapeDefinitions(pinned.xml, sourceManifest, sourceReconciliation);

    const first = buildPathEmissionContract(ir);
    const second = buildPathEmissionContract(ir);

    expect(first).toEqual(second);
    expect(first).toMatchObject({ schemaVersion: 1, precision: 6 });
    expect(first.profiles).toHaveLength(3);
    expect(first.profiles.map(({ name }: { name: string }) => name)).toEqual([
      'square',
      'wide',
      'tall',
    ]);
    for (const profile of first.profiles) {
      expect(profile.emittedShapes).toBe(186);
      expect(profile.emittedPaths).toBe(319);
      expect(profile.nonEmptyPaths).toBe(profile.emittedPaths);
      expect(profile.nonFiniteTokens).toBe(0);
      expect(profile.sha256).toMatch(/^[a-f0-9]{64}$/);
    }
  }, 20_000);

  it('rejects a corpus contract containing an empty emitted path', () => {
    const ir = {
      schemaVersion: 1,
      shapes: [
        {
          name: 'emptyPath',
          adjustmentGuides: [],
          calculatedGuides: [],
          adjustHandles: [],
          connectionSites: [],
          textRectangle: null,
          paths: [
            {
              width: null,
              height: null,
              fill: 'norm',
              stroke: true,
              extrusionOk: true,
              commands: [],
            },
          ],
        },
      ],
    };

    expect(() => buildPathEmissionContract(ir)).toThrow(/1 empty paths.*square/i);
  });
});

describe('OOXML runtime subset generation', () => {
  const candidate = (
    name: string,
    expectedPathCount: 1 | 3,
    finalStroke = true,
    expectedAdjustments?: readonly {
      name: string;
      defaultValue: number;
      handle: { type: 'polar'; axis: 'radius'; min: number; max: number };
    }[],
  ) => ({
    name,
    expectedPathCount,
    expectedAdjustments,
    expectedPathStyles:
      expectedPathCount === 3
        ? [
            { fill: 'norm', stroke: false, extrusionOk: false },
            { fill: 'none', stroke: true, extrusionOk: false },
            { fill: 'none', stroke: finalStroke, extrusionOk: true },
          ]
        : undefined,
  });
  const singlePathIr = {
    source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
    shapes: [
      {
        name: 'singlePathCandidate',
        adjustmentGuides: [],
        calculatedGuides: [],
        paths: [{ commands: [] }],
      },
    ],
  };

  it('rejects a requested definition that is missing from the pinned source', () => {
    expect(() =>
      buildRuntimeGeometryModule(singlePathIr, [candidate('missingCandidate', 1)]),
    ).toThrow(/missing from source/i);
  });

  it('rejects duplicate production-subset names', () => {
    expect(() =>
      buildRuntimeGeometryModule(singlePathIr, [
        candidate('singlePathCandidate', 1),
        candidate('singlePathCandidate', 1),
      ]),
    ).toThrow(/must be unique/i);
  });

  it('accepts the safe ordered three-path production contract', () => {
    const generated = buildRuntimeGeometryModule(
      {
        source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
        shapes: [
          {
            name: 'multiPathCandidate',
            adjustmentGuides: [],
            calculatedGuides: [],
            paths: [
              { fill: 'norm', stroke: false, extrusionOk: false, commands: [] },
              { fill: 'none', stroke: true, extrusionOk: false, commands: [] },
              { fill: 'none', stroke: false, extrusionOk: true, commands: [] },
            ],
          },
        ],
      },
      [candidate('multiPathCandidate', 3, false)],
    );

    expect(generated).toContain('"name": "multiPathCandidate"');
    expect(generated.match(/"commands": \[\]/g)).toHaveLength(3);
  });

  it('rejects a definition whose path count differs from its explicit production contract', () => {
    expect(() =>
      buildRuntimeGeometryModule(
        {
          source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
          shapes: [
            {
              name: 'multiPathCandidate',
              adjustmentGuides: [],
              calculatedGuides: [],
              paths: [
                { fill: 'norm', stroke: false, commands: [] },
                { fill: 'none', stroke: true, commands: [] },
              ],
            },
          ],
        },
        [candidate('multiPathCandidate', 3)],
      ),
    ).toThrow(/expected 3 paths/i);
  });

  it('rejects unsafe multi-path fill and stroke metadata', () => {
    expect(() =>
      buildRuntimeGeometryModule(
        {
          source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
          shapes: [
            {
              name: 'unsafeMultiPathCandidate',
              adjustmentGuides: [],
              calculatedGuides: [],
              paths: [
                { fill: 'norm', stroke: true, commands: [] },
                { fill: 'none', stroke: true, commands: [] },
                { fill: 'none', stroke: true, commands: [] },
              ],
            },
          ],
        },
        [candidate('unsafeMultiPathCandidate', 3)],
      ),
    ).toThrow(/unsupported multi-path style/i);
  });

  it('rejects non-leading stroke drift from the shape-specific path contract', () => {
    expect(() =>
      buildRuntimeGeometryModule(
        {
          source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
          shapes: [
            {
              name: 'strokeDriftCandidate',
              adjustmentGuides: [],
              calculatedGuides: [],
              paths: [
                { fill: 'norm', stroke: false, extrusionOk: false, commands: [] },
                { fill: 'none', stroke: true, extrusionOk: false, commands: [] },
                { fill: 'none', stroke: false, extrusionOk: true, commands: [] },
              ],
            },
          ],
        },
        [candidate('strokeDriftCandidate', 3, true)],
      ),
    ).toThrow(/path 2.*style/i);
  });

  it('rejects extrusion metadata drift from the shape-specific path contract', () => {
    expect(() =>
      buildRuntimeGeometryModule(
        {
          source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
          shapes: [
            {
              name: 'extrusionDriftCandidate',
              adjustmentGuides: [],
              calculatedGuides: [],
              paths: [
                { fill: 'norm', stroke: false, extrusionOk: false, commands: [] },
                { fill: 'none', stroke: true, extrusionOk: false, commands: [] },
                { fill: 'none', stroke: true, extrusionOk: false, commands: [] },
              ],
            },
          ],
        },
        [candidate('extrusionDriftCandidate', 3, true)],
      ),
    ).toThrow(/path 2.*style/i);
  });

  it('rejects adjustment-bearing definitions until adjustment bounds have a production gate', () => {
    expect(() =>
      buildRuntimeGeometryModule(
        {
          source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
          shapes: [
            {
              name: 'adjustedCandidate',
              adjustmentGuides: [{ name: 'adj', formula: { operator: 'val', operands: [] } }],
              calculatedGuides: [],
              paths: [{ commands: [] }],
            },
          ],
        },
        [candidate('adjustedCandidate', 1)],
      ),
    ).toThrow(/adjustment guides/i);
  });

  it('accepts an adjustment only when its default and handle bounds match the contract', () => {
    const generated = buildRuntimeGeometryModule(
      {
        source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
        shapes: [
          {
            name: 'adjustedCandidate',
            adjustmentGuides: [
              {
                name: 'adj',
                formula: {
                  operator: 'val',
                  operands: [{ kind: 'literal', value: 25000 }],
                },
              },
            ],
            calculatedGuides: [],
            adjustHandles: [
              {
                type: 'polar',
                guideRefR: 'adj',
                guideRefAngle: null,
                minR: { kind: 'literal', value: 0 },
                maxR: { kind: 'literal', value: 50000 },
                minAngle: null,
                maxAngle: null,
              },
            ],
            paths: [{ commands: [] }],
          },
        ],
      },
      [
        candidate('adjustedCandidate', 1, true, [
          {
            name: 'adj',
            defaultValue: 25000,
            handle: { type: 'polar', axis: 'radius', min: 0, max: 50000 },
          },
        ]),
      ],
    );

    expect(generated).toContain('"name": "adjustedCandidate"');
    expect(generated).toContain('"value": 25000');
  });

  it.each([
    ['default', 24000, 0, 50000],
    ['minimum', 25000, 1, 50000],
    ['maximum', 25000, 0, 49999],
  ])('rejects adjustment %s drift from the production contract', (_field, value, min, max) => {
    expect(() =>
      buildRuntimeGeometryModule(
        {
          source: { presetShapeDefinitions: { sha256: 'a'.repeat(64) } },
          shapes: [
            {
              name: 'adjustedCandidate',
              adjustmentGuides: [
                {
                  name: 'adj',
                  formula: {
                    operator: 'val',
                    operands: [{ kind: 'literal', value }],
                  },
                },
              ],
              calculatedGuides: [],
              adjustHandles: [
                {
                  type: 'polar',
                  guideRefR: 'adj',
                  guideRefAngle: null,
                  minR: { kind: 'literal', value: min },
                  maxR: { kind: 'literal', value: max },
                  minAngle: null,
                  maxAngle: null,
                },
              ],
              paths: [{ commands: [] }],
            },
          ],
        },
        [
          candidate('adjustedCandidate', 1, true, [
            {
              name: 'adj',
              defaultValue: 25000,
              handle: { type: 'polar', axis: 'radius', min: 0, max: 50000 },
            },
          ]),
        ],
      ),
    ).toThrow(/adjustment.*contract/i);
  });
});
