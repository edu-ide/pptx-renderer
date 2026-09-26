import { describe, expect, it } from 'vitest';

import {
  createPredefinedGuideEnvironment,
  evaluateGuideFormula,
} from '../../../scripts/ooxml-geometry/formula-evaluator.mjs';
import {
  compilePresetShapeDefinitions,
  evaluatePresetShape,
  summarizePresetGeometryIr,
} from '../../../scripts/ooxml-geometry/geometry-ir.mjs';
import { buildGeometryIrContract } from '../../../scripts/ooxml-geometry/generate.mjs';
import { loadPinnedPresetShapeDefinitions } from '../../../scripts/ooxml-geometry/source-validator.mjs';
import sourceManifest from '../../../scripts/ooxml-geometry/source-manifest.json';
import sourceReconciliation from '../../../scripts/ooxml-geometry/source-reconciliation.json';

const repositoryRoot = process.cwd();
const drawingNamespace = 'http://schemas.openxmlformats.org/drawingml/2006/main';

describe('OOXML guide formula evaluator', () => {
  it.each([
    ['*/ 6 7 3', 14],
    ['+- 10 3 4', 9],
    ['+/ 10 2 3', 4],
    ['?: 1 20 30', 20],
    ['?: 0 20 30', 30],
    ['abs -7', 7],
    ['at2 1 1', 2700000],
    ['at2 -1 1', 8100000],
    ['at2 0 0', 0],
    ['cat2 10 3 4', 6],
    ['cos 10 5400000', 0],
    ['max -2 3', 3],
    ['min -2 3', -2],
    ['mod 2 3 6', 7],
    ['pin 0 -1 10', 0],
    ['pin 0 4 10', 4],
    ['pin 0 11 10', 10],
    ['sat2 10 3 4', 8],
    ['sin 10 5400000', 10],
    ['sqrt -9', 3],
    ['tan 10 2700000', 10],
    ['val -2', -2],
  ])('evaluates %s with PowerPoint-compatible semantics', (formula, expected) => {
    expect(evaluateGuideFormula(formula)).toBeCloseTo(expected, 6);
  });

  it('resolves symbolic operands without mutating the caller environment', () => {
    const environment = new Map([['x', 12]]);

    expect(evaluateGuideFormula('*/ x 5 3', environment)).toBe(20);
    expect([...environment]).toEqual([['x', 12]]);
  });

  it('constructs the complete predefined environment from explicit dimensions', () => {
    const environment = createPredefinedGuideEnvironment(400, 240);

    expect(environment.get('l')).toBe(0);
    expect(environment.get('t')).toBe(0);
    expect(environment.get('r')).toBe(400);
    expect(environment.get('b')).toBe(240);
    expect(environment.get('hc')).toBe(200);
    expect(environment.get('vc')).toBe(120);
    expect(environment.get('ss')).toBe(240);
    expect(environment.get('ls')).toBe(400);
    expect(environment.get('wd10')).toBe(40);
    expect(environment.get('hd10')).toBe(24);
    expect(environment.get('3cd4')).toBe(16200000);
  });

  it('rejects missing guides, zero divisors, invalid dimensions, and non-finite results', () => {
    expect(() => evaluateGuideFormula('val missing')).toThrow(/unknown guide missing/i);
    expect(() => evaluateGuideFormula('*/ 1 2 0')).toThrow(/division by zero.*\*\//i);
    expect(() => evaluateGuideFormula('+/ 1 2 0')).toThrow(/division by zero.*\+\//i);
    expect(() =>
      evaluateGuideFormula('*/ huge huge 1', new Map([['huge', Number.MAX_VALUE]])),
    ).toThrow(/non-finite.*\*\//i);
    expect(() => createPredefinedGuideEnvironment(0, 100)).toThrow(/width.*greater than zero/i);
    expect(() => createPredefinedGuideEnvironment(100, Number.NaN)).toThrow(/height.*finite/i);
  });

  it('canonicalizes negative zero for deterministic downstream geometry', () => {
    const value = evaluateGuideFormula('+- 0 0 0');
    expect(value).toBe(0);
    expect(Object.is(value, -0)).toBe(false);
  });

  it.each([
    [
      'extra operands',
      {
        operator: 'val',
        operands: [
          { kind: 'literal', value: 1 },
          { kind: 'literal', value: 2 },
        ],
      },
      /val expects 1 operands.*received 2/i,
    ],
    ['missing operands', { operator: 'sin', operands: [] }, /sin expects 2 operands.*received 0/i],
    [
      'an invalid operand kind',
      { operator: 'val', operands: [{ kind: 'other', name: 'x' }] },
      /operand 1.*invalid kind other/i,
    ],
    [
      'an empty guide name',
      { operator: 'val', operands: [{ kind: 'guide', name: '' }] },
      /operand 1.*guide name.*non-empty/i,
    ],
    [
      'a non-finite literal',
      { operator: 'val', operands: [{ kind: 'literal', value: Number.NaN }] },
      /operand 1.*literal.*finite/i,
    ],
  ])('rejects %s in a caller-provided parsed formula', (_description, formula, expected) => {
    expect(() => evaluateGuideFormula(formula)).toThrow(expected as RegExp);
  });
});

function geometryXml(shapeBodies: string): string {
  return `<?xml version="1.0" encoding="utf-8"?>
    <presetShapeDefinitons>${shapeBodies}</presetShapeDefinitons>`;
}

const completeShapeXml = geometryXml(`
  <completeShape>
    <avLst xmlns="${drawingNamespace}">
      <gd name="adj" fmla="val 25000"/>
    </avLst>
    <gdLst xmlns="${drawingNamespace}">
      <gd name="x1" fmla="*/ w adj 100000"/>
      <gd name="y1" fmla="*/ h 1 2"/>
      <gd name="x1" fmla="+- x1 1 0"/>
    </gdLst>
    <ahLst xmlns="${drawingNamespace}">
      <ahXY gdRefX="adj" minX="0" maxX="100000">
        <pos x="x1" y="y1"/>
      </ahXY>
      <ahPolar gdRefR="adj" minR="0" maxR="100000" minAng="0" maxAng="cd2">
        <pos x="x1" y="y1"/>
      </ahPolar>
    </ahLst>
    <cxnLst xmlns="${drawingNamespace}">
      <cxn ang="cd4"><pos x="x1" y="y1"/></cxn>
    </cxnLst>
    <rect xmlns="${drawingNamespace}" l="l" t="t" r="x1" b="y1"/>
    <pathLst xmlns="${drawingNamespace}">
      <path w="21600" h="21600" fill="lightenLess" stroke="false" extrusionOk="false">
        <moveTo><pt x="x1" y="0"/></moveTo>
        <lnTo><pt x="21600" y="y1"/></lnTo>
        <quadBezTo><pt x="1" y="2"/><pt x="3" y="4"/></quadBezTo>
        <cubicBezTo><pt x="5" y="6"/><pt x="7" y="8"/><pt x="9" y="10"/></cubicBezTo>
        <arcTo wR="x1" hR="y1" stAng="0" swAng="cd4"/>
        <close/>
      </path>
      <path><moveTo><pt x="l" y="t"/></moveTo></path>
    </pathLst>
  </completeShape>`);

describe('renderer-independent OOXML preset geometry IR', () => {
  it('compiles every geometry section and path command into plain data', () => {
    const ir = compilePresetShapeDefinitions(completeShapeXml, sourceManifest, {
      schemaVersion: 1,
      entries: [],
    });
    const shape = ir.shapes[0];

    expect(ir.schemaVersion).toBe(1);
    expect(shape.name).toBe('completeShape');
    expect(shape.adjustmentGuides).toHaveLength(1);
    expect(shape.calculatedGuides.map(({ name }: { name: string }) => name)).toEqual([
      'x1',
      'y1',
      'x1',
    ]);
    expect(shape.adjustHandles).toEqual([
      {
        type: 'xy',
        guideRefX: 'adj',
        guideRefY: null,
        minX: { kind: 'literal', value: 0 },
        maxX: { kind: 'literal', value: 100000 },
        minY: null,
        maxY: null,
        position: {
          x: { kind: 'guide', name: 'x1' },
          y: { kind: 'guide', name: 'y1' },
        },
      },
      expect.objectContaining({
        type: 'polar',
        guideRefR: 'adj',
        guideRefAngle: null,
        minAngle: { kind: 'literal', value: 0 },
        maxAngle: { kind: 'guide', name: 'cd2' },
      }),
    ]);
    expect(shape.connectionSites).toEqual([
      {
        angle: { kind: 'guide', name: 'cd4' },
        position: {
          x: { kind: 'guide', name: 'x1' },
          y: { kind: 'guide', name: 'y1' },
        },
      },
    ]);
    expect(shape.textRectangle).toEqual({
      left: { kind: 'guide', name: 'l' },
      top: { kind: 'guide', name: 't' },
      right: { kind: 'guide', name: 'x1' },
      bottom: { kind: 'guide', name: 'y1' },
    });
    expect(shape.paths[0]).toMatchObject({
      width: { kind: 'literal', value: 21600 },
      height: { kind: 'literal', value: 21600 },
      fill: 'lightenLess',
      stroke: false,
      extrusionOk: false,
    });
    expect(shape.paths[0].commands.map(({ type }: { type: string }) => type)).toEqual([
      'moveTo',
      'lnTo',
      'quadBezTo',
      'cubicBezTo',
      'arcTo',
      'close',
    ]);
    expect(shape.paths[1]).toMatchObject({
      width: null,
      height: null,
      fill: 'norm',
      stroke: true,
      extrusionOk: true,
    });
    expect(() => JSON.stringify(ir)).not.toThrow();
    expect(JSON.stringify(ir)).not.toMatch(/ownerDocument|nodeType/);
  });

  it('evaluates defaults and named overrides while preserving guide rebinding', () => {
    const [shape] = compilePresetShapeDefinitions(completeShapeXml, sourceManifest, {
      schemaVersion: 1,
      entries: [],
    }).shapes;

    const defaults = evaluatePresetShape(shape, { width: 400, height: 200 });
    const overridden = evaluatePresetShape(shape, {
      width: 400,
      height: 200,
      adjustments: { adj: 50000 },
    });

    expect(defaults.adjustments).toEqual({ adj: 25000 });
    expect(defaults.guideValues.x1).toBe(101);
    expect(overridden.adjustments).toEqual({ adj: 50000 });
    expect(overridden.guideValues.x1).toBe(201);
    expect(overridden.paths[0].width).toBe(21600);
    expect(overridden.paths[0].height).toBe(21600);
    expect(overridden.paths[0].commands[0]).toEqual({ type: 'moveTo', x: 201, y: 0 });
    expect(overridden.paths[1].width).toBe(400);
    expect(overridden.paths[1].height).toBe(200);
    expect(overridden.adjustHandles[0]).toMatchObject({
      type: 'xy',
      guideRefX: 'adj',
      position: { x: 201, y: 100 },
      minX: 0,
      maxX: 100000,
    });
    expect(overridden.connectionSites[0]).toEqual({
      angle: 5400000,
      position: { x: 201, y: 100 },
    });
    expect(overridden.textRectangle).toEqual({ left: 0, top: 0, right: 201, bottom: 100 });
  });

  it('rejects invalid extents and unknown or non-finite adjustment overrides', () => {
    const [shape] = compilePresetShapeDefinitions(completeShapeXml, sourceManifest, {
      schemaVersion: 1,
      entries: [],
    }).shapes;

    expect(() => evaluatePresetShape(shape, { width: -1, height: 100 })).toThrow(
      /completeShape.*width.*greater than zero/i,
    );
    expect(() =>
      evaluatePresetShape(shape, { width: 100, height: 100, adjustments: { ghost: 1 } }),
    ).toThrow(/unknown adjustment ghost.*completeShape/i);
    expect(() =>
      evaluatePresetShape(shape, {
        width: 100,
        height: 100,
        adjustments: { adj: Number.POSITIVE_INFINITY },
      }),
    ).toThrow(/completeShape.*adjustment adj.*finite/i);
  });

  it('compiles and evaluates the complete pinned corpus at square, wide, and tall extents', async () => {
    const pinned = await loadPinnedPresetShapeDefinitions(repositoryRoot, sourceManifest);
    const ir = compilePresetShapeDefinitions(pinned.xml, sourceManifest, sourceReconciliation);

    expect(summarizePresetGeometryIr(ir)).toMatchObject({
      shapes: 186,
      adjustmentGuides: 298,
      calculatedGuides: 3612,
      adjustHandles: 241,
      connectionSites: 856,
      paths: 319,
      pathCommands: {
        arcTo: 393,
        close: 319,
        cubicBezTo: 28,
        lnTo: 1689,
        moveTo: 445,
        quadBezTo: 33,
      },
    });

    for (const { width, height } of [
      { width: 216, height: 216 },
      { width: 400, height: 180 },
      { width: 180, height: 400 },
    ]) {
      const failures: string[] = [];
      for (const shape of ir.shapes) {
        try {
          evaluatePresetShape(shape, { width, height });
        } catch (error) {
          failures.push(`${shape.name}: ${(error as Error).message}`);
        }
      }
      expect(failures, `${width}x${height} evaluation failures`).toEqual([]);
    }
  }, 20_000);

  it('builds a deterministic structural fingerprint and evaluation gate for generated output', async () => {
    const pinned = await loadPinnedPresetShapeDefinitions(repositoryRoot, sourceManifest);
    const ir = compilePresetShapeDefinitions(pinned.xml, sourceManifest, sourceReconciliation);

    const first = buildGeometryIrContract(ir);
    const second = buildGeometryIrContract(ir);

    expect(first).toEqual(second);
    expect(first.schemaVersion).toBe(1);
    expect(first.structuralSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(first.summary.shapes).toBe(186);
    expect(first.evaluationProfiles).toEqual([
      { name: 'square', width: 216, height: 216, evaluatedShapes: 186 },
      { name: 'wide', width: 400, height: 180, evaluatedShapes: 186 },
      { name: 'tall', width: 180, height: 400, evaluatedShapes: 186 },
    ]);
  }, 20_000);
});
