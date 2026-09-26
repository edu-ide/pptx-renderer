import { FORMULA_DEFINITIONS, PREDEFINED_GUIDES, parseGuideFormula } from './formula-contract.mjs';

const OOXML_ANGLE_UNITS_PER_DEGREE = 60000;
const OOXML_ANGLE_UNITS_PER_RADIAN = (180 * OOXML_ANGLE_UNITS_PER_DEGREE) / Math.PI;
const RADIANS_PER_OOXML_ANGLE_UNIT = Math.PI / (180 * OOXML_ANGLE_UNITS_PER_DEGREE);
const FORMULA_DEFINITION_BY_TOKEN = new Map(
  FORMULA_DEFINITIONS.map((definition) => [definition.token, definition]),
);

function finiteNumber(value, context) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${context} must be a finite number`);
  }
  return Object.is(value, -0) ? 0 : value;
}

function positiveDimension(value, name) {
  const finite = finiteNumber(value, name);
  if (finite <= 0) {
    throw new Error(`${name} must be greater than zero`);
  }
  return finite;
}

function lookupGuide(environment, name) {
  let value;
  let found = false;
  if (environment instanceof Map) {
    found = environment.has(name);
    value = environment.get(name);
  } else if (environment && Object.prototype.hasOwnProperty.call(environment, name)) {
    found = true;
    value = environment[name];
  }
  if (!found) {
    throw new Error(`Unknown guide ${name}`);
  }
  return finiteNumber(value, `Guide ${name}`);
}

function resolveOperand(operand, environment) {
  if (operand.kind === 'literal') return finiteNumber(operand.value, 'Formula literal');
  return lookupGuide(environment, operand.name);
}

function validateParsedFormula(formula) {
  if (!formula || typeof formula.operator !== 'string' || !Array.isArray(formula.operands)) {
    throw new Error('Guide formula must be a parsed formula or formula string');
  }
  const definition = FORMULA_DEFINITION_BY_TOKEN.get(formula.operator);
  if (!definition) {
    throw new Error(`Unknown guide formula operator: ${formula.operator}`);
  }
  if (formula.operands.length !== definition.arity) {
    throw new Error(
      `Guide formula operator ${formula.operator} expects ${definition.arity} operands; received ${formula.operands.length}`,
    );
  }
  for (const [index, operand] of formula.operands.entries()) {
    const context = `Guide formula operand ${index + 1}`;
    if (!operand || !['literal', 'guide'].includes(operand.kind)) {
      throw new Error(`${context} has invalid kind ${operand?.kind}`);
    }
    if (operand.kind === 'literal') {
      finiteNumber(operand.value, `${context} literal`);
    } else if (typeof operand.name !== 'string' || operand.name.trim().length === 0) {
      throw new Error(`${context} guide name must be non-empty`);
    }
  }
  return formula;
}

function divide(numerator, denominator, operator) {
  if (denominator === 0) {
    throw new Error(`Division by zero while evaluating ${operator}`);
  }
  return numerator / denominator;
}

/**
 * Evaluate one parsed or textual DrawingML guide formula without mutating the guide environment.
 * All intermediate values remain JavaScript binary64 numbers; coordinate formatting belongs to
 * the later path-emission layer.
 *
 * @param {string | ReturnType<typeof parseGuideFormula>} formula
 * @param {ReadonlyMap<string, number> | Record<string, number>} [environment]
 */
export function evaluateGuideFormula(formula, environment = new Map()) {
  const parsed = validateParsedFormula(
    typeof formula === 'string' ? parseGuideFormula(formula) : formula,
  );
  const values = parsed.operands.map((operand) => resolveOperand(operand, environment));
  let result;

  switch (parsed.operator) {
    case '*/':
      result = divide(values[0] * values[1], values[2], '*/');
      break;
    case '+-':
      result = values[0] + values[1] - values[2];
      break;
    case '+/':
      result = divide(values[0] + values[1], values[2], '+/');
      break;
    case '?:':
      result = values[0] > 0 ? values[1] : values[2];
      break;
    case 'abs':
      result = Math.abs(values[0]);
      break;
    case 'at2':
      result =
        values[0] === 0 && values[1] === 0
          ? 0
          : Math.atan2(values[1], values[0]) * OOXML_ANGLE_UNITS_PER_RADIAN;
      break;
    case 'cat2':
      result = values[0] * Math.cos(Math.atan2(values[2], values[1]));
      break;
    case 'cos':
      result = values[0] * Math.cos(values[1] * RADIANS_PER_OOXML_ANGLE_UNIT);
      break;
    case 'max':
      result = Math.max(values[0], values[1]);
      break;
    case 'min':
      result = Math.min(values[0], values[1]);
      break;
    case 'mod':
      result = Math.hypot(values[0], values[1], values[2]);
      break;
    case 'pin':
      result = values[1] < values[0] ? values[0] : values[1] > values[2] ? values[2] : values[1];
      break;
    case 'sat2':
      result = values[0] * Math.sin(Math.atan2(values[2], values[1]));
      break;
    case 'sin':
      result = values[0] * Math.sin(values[1] * RADIANS_PER_OOXML_ANGLE_UNIT);
      break;
    case 'sqrt':
      result = Math.sqrt(Math.abs(values[0]));
      break;
    case 'tan':
      result = values[0] * Math.tan(values[1] * RADIANS_PER_OOXML_ANGLE_UNIT);
      break;
    case 'val':
      [result] = values;
      break;
    default:
      throw new Error(`Unknown guide formula operator: ${parsed.operator}`);
  }

  return finiteNumber(result, `Non-finite result while evaluating ${parsed.operator}`);
}

/**
 * Build a fresh map containing all ECMA preset geometry built-ins for one shape extent.
 * Formula-valued built-ins are dependency-resolved rather than relying on object-key order.
 */
export function createPredefinedGuideEnvironment(width, height) {
  const dimensions = {
    width: positiveDimension(width, 'width'),
    height: positiveDimension(height, 'height'),
  };
  const environment = new Map();
  const pending = new Map();

  for (const [name, definition] of Object.entries(PREDEFINED_GUIDES)) {
    if (typeof definition === 'number') {
      environment.set(name, finiteNumber(definition, `Predefined guide ${name}`));
    } else if (definition.startsWith('input:')) {
      const inputName = definition.slice('input:'.length);
      if (!Object.prototype.hasOwnProperty.call(dimensions, inputName)) {
        throw new Error(`Unknown predefined guide input ${inputName}`);
      }
      environment.set(name, dimensions[inputName]);
    } else {
      pending.set(name, parseGuideFormula(definition));
    }
  }

  while (pending.size > 0) {
    let progressed = false;
    for (const [name, formula] of pending) {
      const ready = formula.operands.every(
        (operand) => operand.kind === 'literal' || environment.has(operand.name),
      );
      if (!ready) continue;
      environment.set(name, evaluateGuideFormula(formula, environment));
      pending.delete(name);
      progressed = true;
    }
    if (!progressed) {
      throw new Error(
        `Predefined guide dependency cycle or missing value: ${[...pending.keys()].join(', ')}`,
      );
    }
  }

  return environment;
}

export const OOXML_ANGLE_CONSTANTS = Object.freeze({
  unitsPerDegree: OOXML_ANGLE_UNITS_PER_DEGREE,
  unitsPerRadian: OOXML_ANGLE_UNITS_PER_RADIAN,
  radiansPerUnit: RADIANS_PER_OOXML_ANGLE_UNIT,
});
