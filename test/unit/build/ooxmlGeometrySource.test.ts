import { describe, expect, it } from 'vitest';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  FORMULA_DEFINITIONS,
  FORMULA_COMPATIBILITY_NOTES,
  FORMULA_NUMERIC_POLICY,
  PREDEFINED_GUIDES,
  parseGuideFormula,
} from '../../../scripts/ooxml-geometry/formula-contract.mjs';
import {
  buildPresetShapeCatalog,
  loadPinnedPresetShapeDefinitions,
  stableJson,
  validateSourceReconciliation,
} from '../../../scripts/ooxml-geometry/source-validator.mjs';
import sourceManifest from '../../../scripts/ooxml-geometry/source-manifest.json';
import sourceReconciliation from '../../../scripts/ooxml-geometry/source-reconciliation.json';
import { writeOrCheckGeneratedCatalog } from '../../../scripts/ooxml-geometry/generate.mjs';

const repositoryRoot = process.cwd();

describe('OOXML guide formula contract', () => {
  it('locks the 17 ECMA-376 operators and their arity', () => {
    expect(
      Object.fromEntries(FORMULA_DEFINITIONS.map(({ token, arity }) => [token, arity])),
    ).toEqual({
      '*/': 3,
      '+-': 3,
      '+/': 3,
      '?:': 3,
      abs: 1,
      at2: 2,
      cat2: 3,
      cos: 2,
      max: 2,
      min: 2,
      mod: 3,
      pin: 3,
      sat2: 3,
      sin: 2,
      sqrt: 1,
      tan: 2,
      val: 1,
    });
  });

  it('parses signed numeric and symbolic operands without changing argument order', () => {
    expect(parseGuideFormula('  at2   w  h ')).toEqual({
      operator: 'at2',
      operands: [
        { kind: 'guide', name: 'w' },
        { kind: 'guide', name: 'h' },
      ],
    });
    expect(parseGuideFormula('+- vc -10800000 2.5e2')).toEqual({
      operator: '+-',
      operands: [
        { kind: 'guide', name: 'vc' },
        { kind: 'literal', value: -10800000 },
        { kind: 'literal', value: 250 },
      ],
    });
  });

  it('rejects unknown operators, wrong arity, empty input, and non-finite literals', () => {
    expect(() => parseGuideFormula('pow x y')).toThrow(/unknown guide formula operator/i);
    expect(() => parseGuideFormula('sin x')).toThrow(/expects 2 operands/i);
    expect(() => parseGuideFormula('   ')).toThrow(/empty guide formula/i);
    expect(() => parseGuideFormula('val 1e999')).toThrow(/finite numeric literal/i);
  });

  it('defines every ECMA preset built-in guide once', () => {
    expect(Object.keys(PREDEFINED_GUIDES)).toEqual([
      '3cd4',
      '3cd8',
      '5cd8',
      '7cd8',
      'b',
      'cd2',
      'cd3',
      'cd4',
      'cd8',
      'h',
      'hc',
      'hd2',
      'hd3',
      'hd4',
      'hd5',
      'hd6',
      'hd8',
      'hd10',
      'l',
      'ls',
      'r',
      'ss',
      'ssd2',
      'ssd4',
      'ssd6',
      'ssd8',
      'ssd16',
      'ssd32',
      't',
      'vc',
      'w',
      'wd2',
      'wd3',
      'wd4',
      'wd5',
      'wd6',
      'wd8',
      'wd10',
      'wd12',
      'wd32',
    ]);
    expect(PREDEFINED_GUIDES['3cd4']).toBe(16200000);
    expect(PREDEFINED_GUIDES.h).toBe('input:height');
    expect(PREDEFINED_GUIDES.wd10).toBe('*/ w 1 10');
  });

  it('keeps exceptional-number and rounding behavior explicit for the future evaluator', () => {
    expect(FORMULA_DEFINITIONS.find(({ token }) => token === 'sqrt')).toMatchObject({
      expression: 'sqrt(abs(x))',
    });
    expect(FORMULA_NUMERIC_POLICY).toEqual({
      angleUnit: '60000ths-of-a-degree',
      intermediateRepresentation: 'IEEE-754 binary64',
      intermediateRounding: 'none',
      outputRounding: 'path-emitter-only',
      divisionByZero: 'evaluation-error',
      negativeSquareRoot: 'absolute-operand',
      nonFiniteResult: 'evaluation-error',
      atan2ZeroZero: 0,
    });
    expect(FORMULA_COMPATIBILITY_NOTES.sourceUrl).toMatch(
      /^https:\/\/learn\.microsoft\.com\/en-us\/openspecs\//,
    );
    expect(Object.keys(FORMULA_COMPATIBILITY_NOTES.deviations).sort()).toEqual([
      'angleUnit',
      'at2',
      'mod',
      'sqrt',
    ]);
  });
});

function geometryXml(shapeBodies: string): string {
  return `<?xml version="1.0" encoding="utf-8"?>
    <presetShapeDefinitons>${shapeBodies}</presetShapeDefinitons>`;
}

function shapeXml(name: string, body: string): string {
  return `<${name}>${body}</${name}>`;
}

const drawingNamespace = 'http://schemas.openxmlformats.org/drawingml/2006/main';

describe('OOXML preset geometry source validation', () => {
  it('verifies the pinned ECMA archive and inventories the complete source deterministically', async () => {
    const pinned = await loadPinnedPresetShapeDefinitions(repositoryRoot, sourceManifest);
    const catalog = buildPresetShapeCatalog(pinned.xml, sourceManifest, sourceReconciliation);

    expect(pinned.archiveSha256).toBe(sourceManifest.geometryArchive.sha256);
    expect(pinned.xmlSha256).toBe(sourceManifest.presetShapeDefinitions.sha256);
    expect(catalog.summary).toMatchObject({
      sourceShapeEntries: 187,
      uniqueShapes: 186,
      guideFormulas: 3910,
      paths: 319,
      multiPathShapes: 63,
      shadowedGuides: 12,
      normalizedFormulas: 8,
    });
    expect(catalog.summary.formulaOperators).toEqual(
      FORMULA_DEFINITIONS.map(({ token }) => token).sort(),
    );
    expect(catalog.diagnostics.identicalDuplicateShapes).toEqual(['upDownArrow']);
    expect(catalog.shapes.some(({ name }: { name: string }) => name === 'upArrow')).toBe(false);
    expect(stableJson(catalog)).toBe(stableJson(catalog));
    expect(stableJson(catalog).endsWith('\n')).toBe(true);
  });

  it('rejects a formula that references a guide before it is defined', () => {
    const xml = geometryXml(
      shapeXml(
        'badForwardRef',
        `<gdLst xmlns="${drawingNamespace}">
          <gd name="first" fmla="val later"/>
          <gd name="later" fmla="val 1"/>
        </gdLst>
        <pathLst xmlns="${drawingNamespace}"><path><moveTo><pt x="0" y="0"/></moveTo></path></pathLst>`,
      ),
    );

    expect(() =>
      buildPresetShapeCatalog(xml, sourceManifest, { schemaVersion: 1, entries: [] }),
    ).toThrow(/badForwardRef.*first.*later.*before it is defined/i);
  });

  it('rejects unsupported path commands and non-identical duplicate shape entries', () => {
    const unsupportedCommand = geometryXml(
      shapeXml(
        'badCommand',
        `<avLst xmlns="${drawingNamespace}"/><gdLst xmlns="${drawingNamespace}"/>
        <pathLst xmlns="${drawingNamespace}"><path><unsupported/></path></pathLst>`,
      ),
    );
    expect(() =>
      buildPresetShapeCatalog(unsupportedCommand, sourceManifest, {
        schemaVersion: 1,
        entries: [],
      }),
    ).toThrow(/badCommand.*unsupported path command/i);

    const duplicate = geometryXml(
      `${shapeXml('sameName', `<pathLst xmlns="${drawingNamespace}"><path/></pathLst>`)}
       ${shapeXml(
         'sameName',
         `<pathLst xmlns="${drawingNamespace}"><path><close/></path></pathLst>`,
       )}`,
    );
    expect(() =>
      buildPresetShapeCatalog(duplicate, sourceManifest, { schemaVersion: 1, entries: [] }),
    ).toThrow(/non-identical duplicate shape.*sameName/i);
  });

  it('allows ordered guide rebinding and records the shadowed name', () => {
    const xml = geometryXml(
      shapeXml(
        'reboundGuide',
        `<gdLst xmlns="${drawingNamespace}">
          <gd name="x1" fmla="val 1"/>
          <gd name="x1" fmla="+- x1 1 0"/>
        </gdLst>
        <pathLst xmlns="${drawingNamespace}"><path><moveTo><pt x="x1" y="0"/></moveTo></path></pathLst>`,
      ),
    );

    const catalog = buildPresetShapeCatalog(xml, sourceManifest, {
      schemaVersion: 1,
      entries: [],
    });
    expect(catalog.diagnostics.shadowedGuides).toEqual([
      { shape: 'reboundGuide', guide: 'x1', occurrence: 2 },
    ]);
  });

  it('requires native-oracle evidence and source provenance before activating an override', () => {
    expect(() =>
      validateSourceReconciliation(
        {
          schemaVersion: 1,
          entries: [
            {
              shape: 'upArrow',
              status: 'override',
              rationale: 'ECMA source omits this preset.',
              override: { sourceUrl: '', sourceSha256: '', oracleCases: [] },
            },
          ],
        },
        new Set(['rect']),
      ),
    ).toThrow(/override.*sourceUrl.*sourceSha256.*oracleCases/i);

    expect(() =>
      validateSourceReconciliation(
        {
          schemaVersion: 1,
          entries: [
            {
              shape: 'ghost',
              status: 'override',
              rationale: 'Plausible-looking metadata must not activate an unverified source.',
              override: {
                sourceUrl: 'https://example.invalid/ghost.xml',
                sourceSha256: '0'.repeat(64),
                oracleCases: ['does-not-exist'],
              },
            },
          ],
        },
        new Set(['rect']),
      ),
    ).toThrow(/override activation is not supported in M0\/M1/i);

    expect(() =>
      validateSourceReconciliation(sourceReconciliation, new Set(['leftArrow'])),
    ).not.toThrow();
  });

  it('accepts the complete structure of all six DrawingML path commands', () => {
    const xml = geometryXml(
      shapeXml(
        'allCommands',
        `<pathLst xmlns="${drawingNamespace}"><path fill="norm" stroke="true">
          <moveTo><pt x="0" y="0"/></moveTo>
          <lnTo><pt x="1" y="1"/></lnTo>
          <quadBezTo><pt x="2" y="2"/><pt x="3" y="3"/></quadBezTo>
          <cubicBezTo><pt x="4" y="4"/><pt x="5" y="5"/><pt x="6" y="6"/></cubicBezTo>
          <arcTo wR="1" hR="1" stAng="0" swAng="cd4"/>
          <close/>
        </path></pathLst>`,
      ),
    );

    expect(() =>
      buildPresetShapeCatalog(xml, sourceManifest, { schemaVersion: 1, entries: [] }),
    ).not.toThrow();
  });

  it.each([
    ['moveTo', '<moveTo/>', /moveTo.*exactly 1 pt/i],
    ['lnTo', '<lnTo><pt x="0" y="0"/><pt x="1" y="1"/></lnTo>', /lnTo.*exactly 1 pt/i],
    ['quadBezTo', '<quadBezTo><pt x="0" y="0"/></quadBezTo>', /quadBezTo.*exactly 2 pt/i],
    [
      'cubicBezTo',
      '<cubicBezTo><pt x="0" y="0"/><pt x="1" y="1"/></cubicBezTo>',
      /cubicBezTo.*exactly 3 pt/i,
    ],
    ['arcTo', '<arcTo wR="1" hR="1" stAng="0"/>', /arcTo.*swAng/i],
    ['close', '<close><pt x="0" y="0"/></close>', /close.*must not have children/i],
  ])('rejects malformed %s path command structure', (_command, commandXml, expected) => {
    const xml = geometryXml(
      shapeXml(
        'badStructure',
        `<pathLst xmlns="${drawingNamespace}"><path>${commandXml}</path></pathLst>`,
      ),
    );

    expect(() =>
      buildPresetShapeCatalog(xml, sourceManifest, { schemaVersion: 1, entries: [] }),
    ).toThrow(expected as RegExp);
  });

  it.each([
    ['an empty path list', `<pathLst xmlns="${drawingNamespace}"/>`, /at least one path/i],
    [
      'an empty coordinate',
      `<pathLst xmlns="${drawingNamespace}"><path><moveTo><pt x="" y="0"/></moveTo></path></pathLst>`,
      /pt@x.*non-empty/i,
    ],
    [
      'the wrong namespace',
      '<pathLst xmlns="urn:not-drawingml"><path><moveTo><pt x="0" y="0"/></moveTo></path></pathLst>',
      /DrawingML namespace/i,
    ],
    [
      'an invalid fill mode',
      `<pathLst xmlns="${drawingNamespace}"><path fill="bogus"><close/></path></pathLst>`,
      /path@fill.*bogus/i,
    ],
    [
      'an invalid stroke boolean',
      `<pathLst xmlns="${drawingNamespace}"><path stroke="yes"><close/></path></pathLst>`,
      /path@stroke.*yes/i,
    ],
  ])('rejects %s', (_description, body, expected) => {
    const xml = geometryXml(shapeXml('badPath', body));

    expect(() =>
      buildPresetShapeCatalog(xml, sourceManifest, { schemaVersion: 1, entries: [] }),
    ).toThrow(expected as RegExp);
  });

  it.each([
    ['gdLst', `<gdLst xmlns="urn:foreign"><gd name="x1" fmla="val 1"/></gdLst>`],
    [
      'gd',
      `<gdLst xmlns="${drawingNamespace}"><gd xmlns="urn:foreign" name="x1" fmla="val 1"/></gdLst>`,
    ],
    ['rect', '<rect xmlns="urn:foreign" l="0" t="0" r="1" b="1"/>'],
    [
      'adjust handle',
      `<ahLst xmlns="${drawingNamespace}"><ahXY xmlns="urn:foreign" gdRefX="adj"><pos x="0" y="0"/></ahXY></ahLst>`,
    ],
    [
      'connection site',
      `<cxnLst xmlns="${drawingNamespace}"><cxn xmlns="urn:foreign" ang="0"><pos x="0" y="0"/></cxn></cxnLst>`,
    ],
  ])('rejects a foreign-namespace %s element', (_description, foreignElement) => {
    const xml = geometryXml(
      shapeXml(
        'badNamespace',
        `${foreignElement}
        <pathLst xmlns="${drawingNamespace}"><path><moveTo><pt x="0" y="0"/></moveTo></path></pathLst>`,
      ),
    );

    expect(() =>
      buildPresetShapeCatalog(xml, sourceManifest, { schemaVersion: 1, entries: [] }),
    ).toThrow(/badNamespace.*DrawingML namespace/i);
  });

  it('sorts object keys recursively so generated JSON has a stable byte representation', () => {
    expect(stableJson({ z: 1, nested: { b: 2, a: 1 }, array: [{ y: 2, x: 1 }] })).toBe(
      '{\n  "array": [\n    {\n      "x": 1,\n      "y": 2\n    }\n  ],\n  "nested": {\n    "a": 1,\n    "b": 2\n  },\n  "z": 1\n}\n',
    );
  });
});

describe('OOXML geometry catalog CLI', () => {
  it('generates stable output and detects a stale generated file in check mode', async () => {
    const temporaryDirectory = mkdtempSync(join(tmpdir(), 'pptx-geometry-catalog-'));
    const outputPath = join(temporaryDirectory, 'catalog.json');
    const generated = '{\n  "stable": true\n}\n';

    try {
      const written = await writeOrCheckGeneratedCatalog({ generated, outputPath });
      expect(written.status).toBe('generated');
      const firstBytes = readFileSync(outputPath, 'utf8');

      const checked = await writeOrCheckGeneratedCatalog({
        check: true,
        generated,
        outputPath,
      });
      expect(checked.status).toBe('current');
      expect(readFileSync(outputPath, 'utf8')).toBe(firstBytes);

      writeFileSync(outputPath, `${firstBytes}\n`, 'utf8');
      await expect(
        writeOrCheckGeneratedCatalog({ check: true, generated, outputPath }),
      ).rejects.toThrow(/generated OOXML geometry catalog is stale/i);
    } finally {
      rmSync(temporaryDirectory, { recursive: true, force: true });
    }
  });
});
