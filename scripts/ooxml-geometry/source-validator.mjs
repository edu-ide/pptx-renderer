import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

import { JSDOM } from 'jsdom';
import JSZip from 'jszip';

import {
  FORMULA_COMPATIBILITY_NOTES,
  FORMULA_DEFINITIONS,
  FORMULA_NUMERIC_POLICY,
  PREDEFINED_GUIDES,
  parseGuideFormula,
} from './formula-contract.mjs';

const DRAWINGML_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main';
const PATH_COMMANDS = new Set(['moveTo', 'lnTo', 'arcTo', 'quadBezTo', 'cubicBezTo', 'close']);
const PATH_POINT_COUNTS = Object.freeze({ moveTo: 1, lnTo: 1, quadBezTo: 2, cubicBezTo: 3 });
const PATH_FILL_MODES = new Set(['none', 'norm', 'lighten', 'lightenLess', 'darken', 'darkenLess']);
const XML_BOOLEANS = new Set(['true', 'false', '1', '0']);
const REFERENCE_ATTRIBUTES = Object.freeze({
  ahXY: ['gdRefX', 'gdRefY', 'minX', 'maxX', 'minY', 'maxY'],
  ahPolar: ['gdRefR', 'gdRefAng', 'minR', 'maxR', 'minAng', 'maxAng'],
  pos: ['x', 'y'],
  cxn: ['ang'],
  rect: ['l', 't', 'r', 'b'],
  path: ['w', 'h'],
  pt: ['x', 'y'],
  arcTo: ['wR', 'hR', 'stAng', 'swAng'],
});
const NUMERIC_LITERAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const RESOLUTION_STATUSES = new Set(['use-ecma', 'pending-native-oracle', 'override']);

function sha256(data) {
  return createHash('sha256').update(data).digest('hex');
}

function asPath(root) {
  return root instanceof URL ? fileURLToPath(root) : root;
}

function directChild(element, localName) {
  return [...element.children].find((child) => child.localName === localName);
}

function directChildren(element, localName) {
  return [...element.children].filter((child) => child.localName === localName);
}

function normalizedElement(element) {
  return element.outerHTML.replace(/>\s+</g, '><').trim();
}

function assertResolvable(value, availableGuides, context) {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`${context}: value must be non-empty`);
  }
  if (NUMERIC_LITERAL.test(value)) return;
  if (!availableGuides.has(value)) {
    throw new Error(`${context}: guide ${value} is referenced before it is defined`);
  }
}

function assertDrawingMlNamespace(element, context) {
  if (element.namespaceURI !== DRAWINGML_NS) {
    throw new Error(`${context}: expected DrawingML namespace ${DRAWINGML_NS}`);
  }
}

function requiredAttribute(element, attribute, context) {
  const value = element.getAttribute(attribute);
  if (value === null || value.trim().length === 0) {
    throw new Error(`${context}@${attribute}: value must be non-empty`);
  }
  return value;
}

function validatePathCommand(command, availableGuides, shapeName) {
  const context = `${shapeName} ${command.localName}`;
  assertDrawingMlNamespace(command, context);

  const pointCount = PATH_POINT_COUNTS[command.localName];
  if (pointCount !== undefined) {
    const points = [...command.children];
    if (points.length !== pointCount || points.some((point) => point.localName !== 'pt')) {
      throw new Error(`${context} must contain exactly ${pointCount} pt element(s)`);
    }
    for (const point of points) {
      assertDrawingMlNamespace(point, `${context} pt`);
      for (const attribute of ['x', 'y']) {
        assertResolvable(
          requiredAttribute(point, attribute, `${shapeName} pt`),
          availableGuides,
          `${shapeName} pt@${attribute}`,
        );
      }
    }
    return;
  }

  if (command.localName === 'arcTo') {
    if (command.children.length !== 0) {
      throw new Error(`${context} must not have children`);
    }
    for (const attribute of ['wR', 'hR', 'stAng', 'swAng']) {
      assertResolvable(
        requiredAttribute(command, attribute, context),
        availableGuides,
        `${context}@${attribute}`,
      );
    }
    return;
  }

  if (command.localName === 'close' && command.children.length !== 0) {
    throw new Error(`${context} must not have children`);
  }
}

function parseXml(xml) {
  const document = new JSDOM(xml, { contentType: 'application/xml' }).window.document;
  const parserError = document.querySelector('parsererror');
  if (parserError) {
    throw new Error(`Invalid preset geometry XML: ${parserError.textContent?.trim()}`);
  }
  const root = document.documentElement;
  if (!['presetShapeDefinitons', 'presetShapeDefinitions'].includes(root.localName)) {
    throw new Error(`Unexpected preset geometry root: ${root.localName}`);
  }
  if (root.namespaceURI) {
    throw new Error('Preset geometry root must be unnamespaced');
  }
  return root;
}

/**
 * Load the vendored ECMA geometry addendum and verify both archive and XML fingerprints.
 *
 * @param {string | URL} repositoryRoot
 * @param {any} manifest
 */
export async function loadPinnedPresetShapeDefinitions(repositoryRoot, manifest) {
  const archivePath = resolve(asPath(repositoryRoot), manifest.geometryArchive.repositoryPath);
  const archiveBytes = await readFile(archivePath);
  const archiveSha256 = sha256(archiveBytes);
  if (archiveBytes.byteLength !== manifest.geometryArchive.bytes) {
    throw new Error(
      `ECMA geometry archive size mismatch: expected ${manifest.geometryArchive.bytes}, received ${archiveBytes.byteLength}`,
    );
  }
  if (archiveSha256 !== manifest.geometryArchive.sha256) {
    throw new Error(
      `ECMA geometry archive SHA-256 mismatch: expected ${manifest.geometryArchive.sha256}, received ${archiveSha256}`,
    );
  }

  const archive = await JSZip.loadAsync(archiveBytes);
  const entry = archive.file(manifest.presetShapeDefinitions.archiveEntry);
  if (!entry) {
    throw new Error(
      `ECMA geometry archive is missing ${manifest.presetShapeDefinitions.archiveEntry}`,
    );
  }
  const xmlBytes = await entry.async('nodebuffer');
  const xmlSha256 = sha256(xmlBytes);
  if (xmlBytes.byteLength !== manifest.presetShapeDefinitions.bytes) {
    throw new Error(
      `ECMA preset XML size mismatch: expected ${manifest.presetShapeDefinitions.bytes}, received ${xmlBytes.byteLength}`,
    );
  }
  if (xmlSha256 !== manifest.presetShapeDefinitions.sha256) {
    throw new Error(
      `ECMA preset XML SHA-256 mismatch: expected ${manifest.presetShapeDefinitions.sha256}, received ${xmlSha256}`,
    );
  }

  return { archiveSha256, xmlSha256, xml: xmlBytes.toString('utf8') };
}

/**
 * Validate the source-reconciliation registry. M0/M1 records manual comparisons but rejects
 * every active override until source bytes and native-oracle evidence can be verified offline.
 *
 * @param {any} registry
 * @param {Set<string>} availableShapes
 */
export function validateSourceReconciliation(registry, availableShapes) {
  if (registry?.schemaVersion !== 1 || !Array.isArray(registry.entries)) {
    throw new Error('Source reconciliation must use schemaVersion 1 with an entries array');
  }
  const seen = new Set();
  for (const entry of registry.entries) {
    if (!entry?.shape || seen.has(entry.shape)) {
      throw new Error(`Source reconciliation has a missing or duplicate shape: ${entry?.shape}`);
    }
    seen.add(entry.shape);
    if (!RESOLUTION_STATUSES.has(entry.status)) {
      throw new Error(
        `Source reconciliation for ${entry.shape} has invalid status ${entry.status}`,
      );
    }
    if (!entry.rationale) {
      throw new Error(`Source reconciliation for ${entry.shape} requires a rationale`);
    }
    if (entry.references !== undefined) {
      throw new Error(
        `Source reconciliation for ${entry.shape} must label non-gated comparison links as manualReferences`,
      );
    }
    if (entry.manualReferences !== undefined) {
      if (!Array.isArray(entry.manualReferences)) {
        throw new Error(`manualReferences for ${entry.shape} must be an array`);
      }
      for (const reference of entry.manualReferences) {
        if (
          !reference?.name ||
          typeof reference.url !== 'string' ||
          !reference.url.startsWith('https://') ||
          typeof reference.sha256 !== 'string' ||
          !/^[a-f0-9]{64}$/i.test(reference.sha256)
        ) {
          throw new Error(
            `manualReferences for ${entry.shape} require name, HTTPS url, and SHA-256 metadata`,
          );
        }
      }
    }
    if (entry.status === 'use-ecma' && !availableShapes.has(entry.shape)) {
      throw new Error(`Source reconciliation cannot use ECMA for missing shape ${entry.shape}`);
    }
    if (entry.status === 'override') {
      const sourceUrl = entry.override?.sourceUrl;
      const sourceSha256 = entry.override?.sourceSha256;
      const oracleCases = entry.override?.oracleCases;
      if (
        typeof sourceUrl !== 'string' ||
        sourceUrl.length === 0 ||
        typeof sourceSha256 !== 'string' ||
        !/^[a-f0-9]{64}$/i.test(sourceSha256) ||
        !Array.isArray(oracleCases) ||
        oracleCases.length === 0
      ) {
        throw new Error(
          `Override for ${entry.shape} requires sourceUrl, sourceSha256, and oracleCases`,
        );
      }
      throw new Error(
        `Override activation is not supported in M0/M1; source bytes and native-oracle evidence need an offline verifier`,
      );
    } else if (entry.override !== undefined) {
      throw new Error(
        `Source reconciliation for ${entry.shape} may only define override at status override`,
      );
    }
  }

  const normalizations = registry.formulaNormalizations ?? [];
  if (!Array.isArray(normalizations)) {
    throw new Error('Source reconciliation formulaNormalizations must be an array');
  }
  const normalizationKeys = new Set();
  for (const normalization of normalizations) {
    const key = `${normalization?.shape}\u0000${normalization?.guide}\u0000${normalization?.original}`;
    if (
      !normalization?.shape ||
      !normalization?.guide ||
      !normalization?.original ||
      !normalization?.normalized ||
      !normalization?.rationale ||
      normalizationKeys.has(key)
    ) {
      throw new Error('Source reconciliation has an incomplete or duplicate formula normalization');
    }
    normalizationKeys.add(key);
    parseGuideFormula(normalization.normalized);
  }
}

function countMap(map) {
  return Object.fromEntries(
    [...map.entries()].sort(([left], [right]) => left.localeCompare(right)),
  );
}

function increment(map, key, amount = 1) {
  map.set(key, (map.get(key) ?? 0) + amount);
}

function inspectShape(shape, diagnostics, formulaNormalizations, usedNormalizations) {
  const name = shape.localName;
  if (shape.namespaceURI) {
    throw new Error(`${name}: top-level shape entry must be unnamespaced`);
  }
  for (const element of shape.querySelectorAll('*')) {
    assertDrawingMlNamespace(element, `${name} ${element.localName}`);
  }
  const availableGuides = new Set(Object.keys(PREDEFINED_GUIDES));
  const guideOccurrences = new Map();
  const operatorUsage = new Map();
  const guideNames = [];
  let adjustmentGuides = 0;
  let calculatedGuides = 0;

  for (const [sectionName, countField] of [
    ['avLst', 'adjustment'],
    ['gdLst', 'calculated'],
  ]) {
    const section = directChild(shape, sectionName);
    if (!section) continue;
    for (const guide of directChildren(section, 'gd')) {
      const guideName = guide.getAttribute('name');
      const formula = guide.getAttribute('fmla');
      if (!guideName || !formula) {
        throw new Error(`${name}: ${sectionName} guide requires name and fmla`);
      }
      let parsed;
      try {
        parsed = parseGuideFormula(formula);
      } catch (error) {
        const normalizationKey = `${name}\u0000${guideName}\u0000${formula}`;
        const normalization = formulaNormalizations.get(normalizationKey);
        if (!normalization) {
          throw new Error(`${name} guide ${guideName}: ${error.message}`);
        }
        parsed = parseGuideFormula(normalization.normalized);
        usedNormalizations.add(normalizationKey);
        diagnostics.normalizedFormulas.push({
          shape: name,
          guide: guideName,
          original: formula,
          normalized: normalization.normalized,
        });
      }
      for (const operand of parsed.operands) {
        if (operand.kind === 'guide') {
          assertResolvable(operand.name, availableGuides, `${name} guide ${guideName}`);
        }
      }
      increment(operatorUsage, parsed.operator);
      const occurrence = (guideOccurrences.get(guideName) ?? 0) + 1;
      guideOccurrences.set(guideName, occurrence);
      if (occurrence > 1) {
        diagnostics.shadowedGuides.push({ shape: name, guide: guideName, occurrence });
      }
      availableGuides.add(guideName);
      guideNames.push(guideName);
      if (countField === 'adjustment') adjustmentGuides += 1;
      else calculatedGuides += 1;
    }
  }

  const referenceElements = [shape, ...shape.querySelectorAll('*')];
  for (const element of referenceElements) {
    const attributes = REFERENCE_ATTRIBUTES[element.localName];
    if (!attributes) continue;
    for (const attribute of attributes) {
      const value = element.getAttribute(attribute);
      if (value !== null) {
        assertResolvable(value, availableGuides, `${name} ${element.localName}@${attribute}`);
      }
    }
  }

  const pathList = directChild(shape, 'pathLst');
  if (!pathList) {
    throw new Error(`${name}: missing pathLst`);
  }
  assertDrawingMlNamespace(pathList, `${name} pathLst`);
  const pathChildren = [...pathList.children];
  if (pathChildren.some((path) => path.localName !== 'path')) {
    throw new Error(`${name}: pathLst may only contain path elements`);
  }
  const paths = pathChildren;
  if (paths.length === 0) {
    throw new Error(`${name}: pathLst requires at least one path`);
  }
  const commandUsage = new Map();
  const fillUsage = new Map();
  const strokeUsage = new Map();
  for (const path of paths) {
    assertDrawingMlNamespace(path, `${name} path`);
    const fill = path.getAttribute('fill') ?? 'norm';
    const stroke = path.getAttribute('stroke') ?? 'true';
    const extrusionOk = path.getAttribute('extrusionOk');
    if (!PATH_FILL_MODES.has(fill)) {
      throw new Error(`${name} path@fill has invalid value ${fill}`);
    }
    if (!XML_BOOLEANS.has(stroke)) {
      throw new Error(`${name} path@stroke has invalid value ${stroke}`);
    }
    if (extrusionOk !== null && !XML_BOOLEANS.has(extrusionOk)) {
      throw new Error(`${name} path@extrusionOk has invalid value ${extrusionOk}`);
    }
    increment(fillUsage, fill);
    increment(strokeUsage, stroke);
    if (path.children.length === 0) {
      throw new Error(`${name}: path requires at least one command`);
    }
    for (const command of [...path.children]) {
      if (!PATH_COMMANDS.has(command.localName)) {
        throw new Error(`${name}: unsupported path command ${command.localName}`);
      }
      validatePathCommand(command, availableGuides, name);
      increment(commandUsage, command.localName);
    }
  }

  return {
    name,
    adjustmentGuides,
    calculatedGuides,
    guideNames,
    operatorUsage: countMap(operatorUsage),
    paths: paths.length,
    pathCommands: countMap(commandUsage),
    pathFills: countMap(fillUsage),
    pathStrokes: countMap(strokeUsage),
    adjustHandles: shape.querySelectorAll('ahXY, ahPolar').length,
    connectionSites: shape.querySelectorAll('cxn').length,
    hasTextRectangle: shape.querySelector('rect') !== null,
  };
}

/**
 * Validate preset definitions and build a build-time inventory. This does not emit runtime IR.
 *
 * @param {string} xml
 * @param {any} manifest
 * @param {any} reconciliation
 */
export function buildPresetShapeCatalog(xml, manifest, reconciliation) {
  const root = parseXml(xml);
  const isPinnedSource =
    sha256(Buffer.from(xml, 'utf8')) === manifest.presetShapeDefinitions.sha256;
  const sourceEntries = [...root.children];
  const uniqueElements = [];
  const elementByName = new Map();
  const diagnostics = {
    identicalDuplicateShapes: [],
    normalizedFormulas: [],
    shadowedGuides: [],
  };
  const formulaNormalizations = new Map(
    (reconciliation.formulaNormalizations ?? []).map((normalization) => [
      `${normalization.shape}\u0000${normalization.guide}\u0000${normalization.original}`,
      normalization,
    ]),
  );
  const usedNormalizations = new Set();

  for (const element of sourceEntries) {
    const name = element.localName;
    const existing = elementByName.get(name);
    if (!existing) {
      elementByName.set(name, element);
      uniqueElements.push(element);
      continue;
    }
    if (normalizedElement(existing) !== normalizedElement(element)) {
      throw new Error(`Non-identical duplicate shape entry: ${name}`);
    }
    diagnostics.identicalDuplicateShapes.push(name);
  }

  const shapes = uniqueElements.map((shape) =>
    inspectShape(shape, diagnostics, formulaNormalizations, usedNormalizations),
  );
  const availableShapes = new Set(shapes.map(({ name }) => name));
  validateSourceReconciliation(reconciliation, availableShapes);
  if (isPinnedSource && usedNormalizations.size !== formulaNormalizations.size) {
    const stale = [...formulaNormalizations.keys()].filter((key) => !usedNormalizations.has(key));
    throw new Error(`Pinned-source formula normalizations were not used: ${stale.join(', ')}`);
  }

  const aggregateOperators = new Map();
  let guideFormulas = 0;
  let paths = 0;
  let multiPathShapes = 0;
  for (const shape of shapes) {
    guideFormulas += shape.adjustmentGuides + shape.calculatedGuides;
    paths += shape.paths;
    if (shape.paths > 1) multiPathShapes += 1;
    for (const [operator, count] of Object.entries(shape.operatorUsage)) {
      increment(aggregateOperators, operator, count);
    }
  }

  const usedOperators = [...aggregateOperators.keys()].sort();
  const contractOperators = FORMULA_DEFINITIONS.map(({ token }) => token).sort();
  if (isPinnedSource && usedOperators.join('\u0000') !== contractOperators.join('\u0000')) {
    throw new Error(
      `Formula operator coverage mismatch: expected ${contractOperators.join(', ')}, received ${usedOperators.join(', ')}`,
    );
  }

  return {
    schemaVersion: 1,
    source: {
      standard: manifest.standard,
      geometryArchive: manifest.geometryArchive,
      presetShapeDefinitions: manifest.presetShapeDefinitions,
    },
    formulaContract: {
      compatibilityNotes: FORMULA_COMPATIBILITY_NOTES,
      definitions: FORMULA_DEFINITIONS,
      numericPolicy: FORMULA_NUMERIC_POLICY,
      predefinedGuides: PREDEFINED_GUIDES,
    },
    reconciliation,
    summary: {
      sourceShapeEntries: sourceEntries.length,
      uniqueShapes: shapes.length,
      guideFormulas,
      paths,
      multiPathShapes,
      shadowedGuides: diagnostics.shadowedGuides.length,
      normalizedFormulas: diagnostics.normalizedFormulas.length,
      formulaOperators: usedOperators,
      operatorUsage: countMap(aggregateOperators),
    },
    diagnostics,
    shapes,
  };
}

function sortRecursively(value) {
  if (Array.isArray(value)) return value.map(sortRecursively);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sortRecursively(value[key])]),
    );
  }
  return value;
}

export function stableJson(value) {
  return `${JSON.stringify(sortRecursively(value), null, 2)}\n`;
}
