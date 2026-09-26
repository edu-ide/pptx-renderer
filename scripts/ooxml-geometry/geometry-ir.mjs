import { JSDOM } from 'jsdom';

import { parseGuideFormula } from './formula-contract.mjs';
import { createPredefinedGuideEnvironment, evaluateGuideFormula } from './formula-evaluator.mjs';
import { buildPresetShapeCatalog } from './source-validator.mjs';

function directChild(element, localName) {
  return [...element.children].find((child) => child.localName === localName) ?? null;
}

function directChildren(element, localName) {
  return [...element.children].filter((child) => child.localName === localName);
}

function parseXml(xml) {
  const document = new JSDOM(xml, { contentType: 'application/xml' }).window.document;
  const parserError = document.querySelector('parsererror');
  if (parserError) {
    throw new Error(`Invalid preset geometry XML: ${parserError.textContent?.trim()}`);
  }
  return document.documentElement;
}

function requiredAttribute(element, name, context) {
  const value = element.getAttribute(name);
  if (value === null || value.trim().length === 0) {
    throw new Error(`${context}@${name}: value must be non-empty`);
  }
  return value;
}

function reference(value) {
  return parseGuideFormula(`val ${value}`).operands[0];
}

function optionalReference(element, attribute) {
  const value = element.getAttribute(attribute);
  return value === null ? null : reference(value);
}

function position(element, context) {
  const pos = directChild(element, 'pos');
  if (!pos) throw new Error(`${context}: missing pos`);
  return {
    x: reference(requiredAttribute(pos, 'x', `${context} pos`)),
    y: reference(requiredAttribute(pos, 'y', `${context} pos`)),
  };
}

function normalizationMap(reconciliation) {
  return new Map(
    (reconciliation.formulaNormalizations ?? []).map((normalization) => [
      `${normalization.shape}\u0000${normalization.guide}\u0000${normalization.original}`,
      normalization.normalized,
    ]),
  );
}

function compileGuide(guide, shapeName, normalizations) {
  const name = requiredAttribute(guide, 'name', `${shapeName} guide`);
  const sourceFormula = requiredAttribute(guide, 'fmla', `${shapeName} guide ${name}`);
  let formula;
  let normalizedFrom = null;
  try {
    formula = parseGuideFormula(sourceFormula);
  } catch (error) {
    const normalized = normalizations.get(`${shapeName}\u0000${name}\u0000${sourceFormula}`);
    if (!normalized) throw error;
    formula = parseGuideFormula(normalized);
    normalizedFrom = sourceFormula;
  }
  return { name, formula, normalizedFrom };
}

function compileAdjustHandle(handle, shapeName) {
  const context = `${shapeName} ${handle.localName}`;
  if (handle.localName === 'ahXY') {
    return {
      type: 'xy',
      guideRefX: handle.getAttribute('gdRefX'),
      guideRefY: handle.getAttribute('gdRefY'),
      minX: optionalReference(handle, 'minX'),
      maxX: optionalReference(handle, 'maxX'),
      minY: optionalReference(handle, 'minY'),
      maxY: optionalReference(handle, 'maxY'),
      position: position(handle, context),
    };
  }
  return {
    type: 'polar',
    guideRefR: handle.getAttribute('gdRefR'),
    guideRefAngle: handle.getAttribute('gdRefAng'),
    minR: optionalReference(handle, 'minR'),
    maxR: optionalReference(handle, 'maxR'),
    minAngle: optionalReference(handle, 'minAng'),
    maxAngle: optionalReference(handle, 'maxAng'),
    position: position(handle, context),
  };
}

function compileCommand(command, shapeName) {
  const context = `${shapeName} ${command.localName}`;
  const points = directChildren(command, 'pt').map((point) => ({
    x: reference(requiredAttribute(point, 'x', `${context} pt`)),
    y: reference(requiredAttribute(point, 'y', `${context} pt`)),
  }));
  switch (command.localName) {
    case 'moveTo':
    case 'lnTo':
      return { type: command.localName, ...points[0] };
    case 'quadBezTo':
      return { type: command.localName, control: points[0], end: points[1] };
    case 'cubicBezTo':
      return {
        type: command.localName,
        control1: points[0],
        control2: points[1],
        end: points[2],
      };
    case 'arcTo':
      return {
        type: command.localName,
        widthRadius: reference(requiredAttribute(command, 'wR', context)),
        heightRadius: reference(requiredAttribute(command, 'hR', context)),
        startAngle: reference(requiredAttribute(command, 'stAng', context)),
        sweepAngle: reference(requiredAttribute(command, 'swAng', context)),
      };
    case 'close':
      return { type: command.localName };
    default:
      throw new Error(`${shapeName}: unsupported path command ${command.localName}`);
  }
}

function xmlBoolean(value, fallback) {
  if (value === null) return fallback;
  return value === 'true' || value === '1';
}

function compileShape(shape, normalizations) {
  const name = shape.localName;
  const adjustmentList = directChild(shape, 'avLst');
  const guideList = directChild(shape, 'gdLst');
  const handleList = directChild(shape, 'ahLst');
  const connectionList = directChild(shape, 'cxnLst');
  const rectangle = directChild(shape, 'rect');
  const pathList = directChild(shape, 'pathLst');

  return {
    name,
    adjustmentGuides: adjustmentList
      ? directChildren(adjustmentList, 'gd').map((guide) =>
          compileGuide(guide, name, normalizations),
        )
      : [],
    calculatedGuides: guideList
      ? directChildren(guideList, 'gd').map((guide) => compileGuide(guide, name, normalizations))
      : [],
    adjustHandles: handleList
      ? [...handleList.children].map((handle) => compileAdjustHandle(handle, name))
      : [],
    connectionSites: connectionList
      ? directChildren(connectionList, 'cxn').map((connection) => ({
          angle: reference(requiredAttribute(connection, 'ang', `${name} cxn`)),
          position: position(connection, `${name} cxn`),
        }))
      : [],
    textRectangle: rectangle
      ? {
          left: reference(requiredAttribute(rectangle, 'l', `${name} rect`)),
          top: reference(requiredAttribute(rectangle, 't', `${name} rect`)),
          right: reference(requiredAttribute(rectangle, 'r', `${name} rect`)),
          bottom: reference(requiredAttribute(rectangle, 'b', `${name} rect`)),
        }
      : null,
    paths: directChildren(pathList, 'path').map((path) => ({
      width: optionalReference(path, 'w'),
      height: optionalReference(path, 'h'),
      fill: path.getAttribute('fill') ?? 'norm',
      stroke: xmlBoolean(path.getAttribute('stroke'), true),
      extrusionOk: xmlBoolean(path.getAttribute('extrusionOk'), true),
      commands: [...path.children].map((command) => compileCommand(command, name)),
    })),
  };
}

/**
 * Compile validated preset definitions into plain, renderer-independent data. Validation runs
 * before compilation so malformed source cannot be converted into a partially usable IR.
 */
export function compilePresetShapeDefinitions(xml, manifest, reconciliation) {
  const catalog = buildPresetShapeCatalog(xml, manifest, reconciliation);
  const root = parseXml(xml);
  const seen = new Set();
  const normalizations = normalizationMap(reconciliation);
  const shapes = [];
  for (const shape of root.children) {
    if (seen.has(shape.localName)) continue;
    seen.add(shape.localName);
    shapes.push(compileShape(shape, normalizations));
  }
  return {
    schemaVersion: 1,
    source: catalog.source,
    diagnostics: catalog.diagnostics,
    shapes,
  };
}

function resolveReference(ref, environment, context) {
  if (ref === null) return null;
  if (ref.kind === 'literal') return ref.value;
  if (!environment.has(ref.name)) {
    throw new Error(`${context}: unknown guide ${ref.name}`);
  }
  const value = environment.get(ref.name);
  if (!Number.isFinite(value)) {
    throw new Error(`${context}: guide ${ref.name} must be finite`);
  }
  return Object.is(value, -0) ? 0 : value;
}

function evaluatePoint(point, environment, context) {
  return {
    x: resolveReference(point.x, environment, `${context} x`),
    y: resolveReference(point.y, environment, `${context} y`),
  };
}

function evaluateCommand(command, environment, context) {
  switch (command.type) {
    case 'moveTo':
    case 'lnTo':
      return { type: command.type, ...evaluatePoint(command, environment, context) };
    case 'quadBezTo':
      return {
        type: command.type,
        control: evaluatePoint(command.control, environment, `${context} control`),
        end: evaluatePoint(command.end, environment, `${context} end`),
      };
    case 'cubicBezTo':
      return {
        type: command.type,
        control1: evaluatePoint(command.control1, environment, `${context} control1`),
        control2: evaluatePoint(command.control2, environment, `${context} control2`),
        end: evaluatePoint(command.end, environment, `${context} end`),
      };
    case 'arcTo':
      return {
        type: command.type,
        widthRadius: resolveReference(command.widthRadius, environment, `${context} widthRadius`),
        heightRadius: resolveReference(
          command.heightRadius,
          environment,
          `${context} heightRadius`,
        ),
        startAngle: resolveReference(command.startAngle, environment, `${context} startAngle`),
        sweepAngle: resolveReference(command.sweepAngle, environment, `${context} sweepAngle`),
      };
    case 'close':
      return { type: command.type };
    default:
      throw new Error(`${context}: unsupported IR command ${command.type}`);
  }
}

function finiteAdjustment(value, name) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`Adjustment ${name} must be finite`);
  }
  return Object.is(value, -0) ? 0 : value;
}

function evaluateHandle(handle, environment, context) {
  if (handle.type === 'xy') {
    return {
      type: handle.type,
      guideRefX: handle.guideRefX,
      guideRefY: handle.guideRefY,
      minX: resolveReference(handle.minX, environment, `${context} minX`),
      maxX: resolveReference(handle.maxX, environment, `${context} maxX`),
      minY: resolveReference(handle.minY, environment, `${context} minY`),
      maxY: resolveReference(handle.maxY, environment, `${context} maxY`),
      position: evaluatePoint(handle.position, environment, `${context} position`),
    };
  }
  return {
    type: handle.type,
    guideRefR: handle.guideRefR,
    guideRefAngle: handle.guideRefAngle,
    minR: resolveReference(handle.minR, environment, `${context} minR`),
    maxR: resolveReference(handle.maxR, environment, `${context} maxR`),
    minAngle: resolveReference(handle.minAngle, environment, `${context} minAngle`),
    maxAngle: resolveReference(handle.maxAngle, environment, `${context} maxAngle`),
    position: evaluatePoint(handle.position, environment, `${context} position`),
  };
}

/** Evaluate one compiled definition for a concrete extent and optional adjustment overrides. */
export function evaluatePresetShape(shape, { width, height, adjustments = {} }) {
  let environment;
  try {
    environment = createPredefinedGuideEnvironment(width, height);
  } catch (error) {
    throw new Error(`${shape.name}: ${error.message}`);
  }
  const adjustmentNames = new Set(shape.adjustmentGuides.map(({ name }) => name));
  for (const name of Object.keys(adjustments)) {
    if (!adjustmentNames.has(name)) {
      throw new Error(`Unknown adjustment ${name} for ${shape.name}`);
    }
  }

  const evaluatedAdjustments = {};
  const guideValues = {};
  for (const guide of shape.adjustmentGuides) {
    let value;
    try {
      value = Object.prototype.hasOwnProperty.call(adjustments, guide.name)
        ? finiteAdjustment(adjustments[guide.name], guide.name)
        : evaluateGuideFormula(guide.formula, environment);
    } catch (error) {
      throw new Error(`${shape.name} adjustment ${guide.name}: ${error.message}`);
    }
    environment.set(guide.name, value);
    evaluatedAdjustments[guide.name] = value;
    guideValues[guide.name] = value;
  }

  for (const guide of shape.calculatedGuides) {
    let value;
    try {
      value = evaluateGuideFormula(guide.formula, environment);
    } catch (error) {
      throw new Error(`${shape.name} guide ${guide.name}: ${error.message}`);
    }
    environment.set(guide.name, value);
    guideValues[guide.name] = value;
  }

  const evaluatedPaths = shape.paths.map((path, pathIndex) => ({
    width:
      path.width === null
        ? width
        : resolveReference(path.width, environment, `${shape.name} path ${pathIndex} width`),
    height:
      path.height === null
        ? height
        : resolveReference(path.height, environment, `${shape.name} path ${pathIndex} height`),
    fill: path.fill,
    stroke: path.stroke,
    extrusionOk: path.extrusionOk,
    commands: path.commands.map((command, commandIndex) =>
      evaluateCommand(
        command,
        environment,
        `${shape.name} path ${pathIndex} command ${commandIndex}`,
      ),
    ),
  }));

  return {
    name: shape.name,
    width,
    height,
    adjustments: evaluatedAdjustments,
    guideValues,
    adjustHandles: shape.adjustHandles.map((handle, index) =>
      evaluateHandle(handle, environment, `${shape.name} handle ${index}`),
    ),
    connectionSites: shape.connectionSites.map((connection, index) => ({
      angle: resolveReference(
        connection.angle,
        environment,
        `${shape.name} connection ${index} angle`,
      ),
      position: evaluatePoint(
        connection.position,
        environment,
        `${shape.name} connection ${index} position`,
      ),
    })),
    textRectangle:
      shape.textRectangle === null
        ? null
        : {
            left: resolveReference(
              shape.textRectangle.left,
              environment,
              `${shape.name} rect left`,
            ),
            top: resolveReference(shape.textRectangle.top, environment, `${shape.name} rect top`),
            right: resolveReference(
              shape.textRectangle.right,
              environment,
              `${shape.name} rect right`,
            ),
            bottom: resolveReference(
              shape.textRectangle.bottom,
              environment,
              `${shape.name} rect bottom`,
            ),
          },
    paths: evaluatedPaths,
  };
}

export function summarizePresetGeometryIr(ir) {
  const summary = {
    shapes: ir.shapes.length,
    adjustmentGuides: 0,
    calculatedGuides: 0,
    adjustHandles: 0,
    connectionSites: 0,
    textRectangles: 0,
    paths: 0,
    pathCommands: {},
  };
  for (const shape of ir.shapes) {
    summary.adjustmentGuides += shape.adjustmentGuides.length;
    summary.calculatedGuides += shape.calculatedGuides.length;
    summary.adjustHandles += shape.adjustHandles.length;
    summary.connectionSites += shape.connectionSites.length;
    if (shape.textRectangle) summary.textRectangles += 1;
    summary.paths += shape.paths.length;
    for (const path of shape.paths) {
      for (const command of path.commands) {
        summary.pathCommands[command.type] = (summary.pathCommands[command.type] ?? 0) + 1;
      }
    }
  }
  summary.pathCommands = Object.fromEntries(
    Object.entries(summary.pathCommands).sort(([left], [right]) => left.localeCompare(right)),
  );
  return summary;
}
