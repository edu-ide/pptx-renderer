import { OOXML_ANGLE_CONSTANTS } from './formula-evaluator.mjs';

const DEFAULT_PRECISION = 6;
const FULL_CIRCLE_DEGREES = 360;
const MAX_FULL_CIRCLE_SEGMENT_DEGREES = 180;

function finiteNumber(value, context) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${context} must be finite`);
  }
  return Object.is(value, -0) ? 0 : value;
}

function positiveNumber(value, context) {
  const finite = finiteNumber(value, context);
  if (finite <= 0) throw new Error(`${context} must be greater than zero`);
  return finite;
}

function nonNegativeRadius(value, scale, context) {
  const finite = finiteNumber(value, context);
  const tolerance = Number.EPSILON * Math.max(1, scale, Math.abs(finite)) * 64;
  if (finite < -tolerance) throw new Error(`${context} must be non-negative`);
  return finite < 0 ? 0 : finite;
}

function outputPrecision(value) {
  if (!Number.isInteger(value) || value < 0 || value > 12) {
    throw new Error('SVG output precision must be an integer from 0 through 12');
  }
  return value;
}

function formatNumber(value, precision, context) {
  const finite = finiteNumber(value, context);
  const rounded = Number(finite.toFixed(precision));
  return rounded === 0 || Object.is(rounded, -0) ? '0' : String(rounded);
}

function validatePoint(point, context) {
  if (!point || typeof point !== 'object') throw new Error(`${context} must be a point`);
  return {
    x: finiteNumber(point.x, `${context} x`),
    y: finiteNumber(point.y, `${context} y`),
  };
}

function scaledPoint(point, scaleX, scaleY) {
  return { x: point.x * scaleX, y: point.y * scaleY };
}

function serializedPoint(point, precision, context) {
  return `${formatNumber(point.x, precision, `${context} x`)},${formatNumber(
    point.y,
    precision,
    `${context} y`,
  )}`;
}

function requireCursor(cursor, context) {
  if (cursor === null) throw new Error(`${context}: command requires a current point`);
  return cursor;
}

function visualAnglePoint(center, widthRadius, heightRadius, angle) {
  const visualRadians = angle * OOXML_ANGLE_CONSTANTS.radiansPerUnit;
  const parametricAngle = Math.atan2(
    widthRadius * Math.sin(visualRadians),
    heightRadius * Math.cos(visualRadians),
  );
  return {
    x: center.x + widthRadius * Math.cos(parametricAngle),
    y: center.y + heightRadius * Math.sin(parametricAngle),
  };
}

function emitArc({ command, context, cursor, scaleX, scaleY, radiusScale, precision }) {
  const current = requireCursor(cursor, context);
  const widthRadius = nonNegativeRadius(command.widthRadius, radiusScale, `${context} widthRadius`);
  const heightRadius = nonNegativeRadius(
    command.heightRadius,
    radiusScale,
    `${context} heightRadius`,
  );
  const startAngle = finiteNumber(command.startAngle, `${context} startAngle`);
  const sweepAngle = finiteNumber(command.sweepAngle, `${context} sweepAngle`);

  if (widthRadius === 0 || heightRadius === 0 || sweepAngle === 0) {
    return { cursor: current, segments: [] };
  }

  const startOffset = visualAnglePoint({ x: 0, y: 0 }, widthRadius, heightRadius, startAngle);
  const center = {
    x: current.x - startOffset.x,
    y: current.y - startOffset.y,
  };
  const sweepDegrees = sweepAngle / OOXML_ANGLE_CONSTANTS.unitsPerDegree;
  const segmentCount =
    Math.abs(sweepDegrees) >= FULL_CIRCLE_DEGREES
      ? Math.ceil(Math.abs(sweepDegrees) / MAX_FULL_CIRCLE_SEGMENT_DEGREES)
      : 1;
  const segmentSweep = sweepAngle / segmentCount;
  const largeArc = Math.abs(segmentSweep / OOXML_ANGLE_CONSTANTS.unitsPerDegree) > 180 ? 1 : 0;
  const sweep = segmentSweep > 0 ? 1 : 0;
  const scaledRadii = {
    x: widthRadius * scaleX,
    y: heightRadius * scaleY,
  };
  const radiusText = serializedPoint(scaledRadii, precision, `${context} radii`);
  const segments = [];
  let end = current;

  for (let index = 1; index <= segmentCount; index += 1) {
    end = visualAnglePoint(center, widthRadius, heightRadius, startAngle + segmentSweep * index);
    const scaledEnd = scaledPoint(end, scaleX, scaleY);
    segments.push(
      `A${radiusText} 0 ${largeArc},${sweep} ${serializedPoint(
        scaledEnd,
        precision,
        `${context} endpoint ${index}`,
      )}`,
    );
  }

  return { cursor: end, segments };
}

function emitPath(path, shape, pathIndex, precision) {
  const pathContext = `${shape.name} path ${pathIndex}`;
  const pathWidth = positiveNumber(path?.width, `${pathContext} width`);
  const pathHeight = positiveNumber(path?.height, `${pathContext} height`);
  if (!Array.isArray(path.commands)) throw new Error(`${pathContext} commands must be an array`);

  const scaleX = shape.width / pathWidth;
  const scaleY = shape.height / pathHeight;
  const segments = [];
  let cursor = null;
  let subpathStart = null;

  for (const [commandIndex, command] of path.commands.entries()) {
    const context = `${pathContext} command ${commandIndex}`;
    if (!command || typeof command.type !== 'string') {
      throw new Error(`${context}: command type must be a string`);
    }
    switch (command.type) {
      case 'moveTo': {
        cursor = validatePoint(command, context);
        subpathStart = cursor;
        segments.push(
          `M${serializedPoint(scaledPoint(cursor, scaleX, scaleY), precision, context)}`,
        );
        break;
      }
      case 'lnTo': {
        requireCursor(cursor, context);
        cursor = validatePoint(command, context);
        segments.push(
          `L${serializedPoint(scaledPoint(cursor, scaleX, scaleY), precision, context)}`,
        );
        break;
      }
      case 'quadBezTo': {
        requireCursor(cursor, context);
        const control = validatePoint(command.control, `${context} control`);
        const end = validatePoint(command.end, `${context} end`);
        segments.push(
          `Q${serializedPoint(
            scaledPoint(control, scaleX, scaleY),
            precision,
            `${context} control`,
          )} ${serializedPoint(scaledPoint(end, scaleX, scaleY), precision, `${context} end`)}`,
        );
        cursor = end;
        break;
      }
      case 'cubicBezTo': {
        requireCursor(cursor, context);
        const control1 = validatePoint(command.control1, `${context} control1`);
        const control2 = validatePoint(command.control2, `${context} control2`);
        const end = validatePoint(command.end, `${context} end`);
        segments.push(
          `C${serializedPoint(
            scaledPoint(control1, scaleX, scaleY),
            precision,
            `${context} control1`,
          )} ${serializedPoint(
            scaledPoint(control2, scaleX, scaleY),
            precision,
            `${context} control2`,
          )} ${serializedPoint(scaledPoint(end, scaleX, scaleY), precision, `${context} end`)}`,
        );
        cursor = end;
        break;
      }
      case 'arcTo': {
        const emitted = emitArc({
          command,
          context,
          cursor,
          scaleX,
          scaleY,
          radiusScale: Math.max(pathWidth, pathHeight),
          precision,
        });
        cursor = emitted.cursor;
        segments.push(...emitted.segments);
        break;
      }
      case 'close': {
        if (subpathStart === null) throw new Error(`${context}: close requires a subpath start`);
        segments.push('Z');
        cursor = subpathStart;
        break;
      }
      default:
        throw new Error(`${context}: unsupported path command ${command.type}`);
    }
  }

  return {
    d: segments.join(' '),
    fill: path.fill,
    stroke: path.stroke,
    extrusionOk: path.extrusionOk,
  };
}

/**
 * Convert one evaluated preset-shape IR object into renderer-independent SVG path data.
 * Theme colors, masks, markers, effects, and DOM ownership remain renderer responsibilities.
 */
export function emitPresetShapePaths(shape, { precision = DEFAULT_PRECISION } = {}) {
  if (!shape || typeof shape !== 'object') throw new Error('Evaluated shape must be an object');
  if (typeof shape.name !== 'string' || shape.name.trim().length === 0) {
    throw new Error('Evaluated shape name must be non-empty');
  }
  const width = positiveNumber(shape.width, `${shape.name} width`);
  const height = positiveNumber(shape.height, `${shape.name} height`);
  const digits = outputPrecision(precision);
  if (!Array.isArray(shape.paths)) throw new Error(`${shape.name} paths must be an array`);

  const normalizedShape = { ...shape, width, height };
  return {
    name: shape.name,
    width,
    height,
    paths: shape.paths.map((path, index) => emitPath(path, normalizedShape, index, digits)),
  };
}

export function summarizeEmittedPresetShape(emitted) {
  if (!emitted || !Array.isArray(emitted.paths)) {
    throw new Error('Emitted preset shape paths must be an array');
  }
  const commandCounts = {};
  let nonFiniteTokens = 0;
  let nonEmptyPaths = 0;
  for (const path of emitted.paths) {
    const d = String(path.d ?? '');
    if (d.length > 0) nonEmptyPaths += 1;
    nonFiniteTokens += (d.match(/NaN|Infinity/g) ?? []).length;
    for (const command of d.match(/[MLQCAZ]/g) ?? []) {
      commandCounts[command] = (commandCounts[command] ?? 0) + 1;
    }
  }
  return {
    paths: emitted.paths.length,
    nonEmptyPaths,
    nonFiniteTokens,
    commands: Object.fromEntries(
      Object.entries(commandCounts).sort(([left], [right]) => left.localeCompare(right)),
    ),
  };
}
