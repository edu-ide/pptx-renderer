/**
 * Build-time PowerPoint-compatible contract for ECMA-376 DrawingML geometry guide formulas.
 *
 * This module owns grammar, operand order, built-in guide names, and exceptional-number policy.
 * The separate M1 evaluator implements this contract without coupling it to renderer code.
 */

export const FORMULA_DEFINITIONS = Object.freeze([
  { token: '*/', arity: 3, expression: '(x * y) / z' },
  { token: '+-', arity: 3, expression: '(x + y) - z' },
  { token: '+/', arity: 3, expression: '(x + y) / z' },
  { token: '?:', arity: 3, expression: 'x > 0 ? y : z' },
  { token: 'abs', arity: 1, expression: 'abs(x)' },
  { token: 'at2', arity: 2, expression: 'atan2(y, x)' },
  { token: 'cat2', arity: 3, expression: 'x * cos(atan2(z, y))' },
  { token: 'cos', arity: 2, expression: 'x * cos(y)' },
  { token: 'max', arity: 2, expression: 'max(x, y)' },
  { token: 'min', arity: 2, expression: 'min(x, y)' },
  { token: 'mod', arity: 3, expression: 'sqrt(x^2 + y^2 + z^2)' },
  { token: 'pin', arity: 3, expression: 'x if y < x; z if y > z; otherwise y' },
  { token: 'sat2', arity: 3, expression: 'x * sin(atan2(z, y))' },
  { token: 'sin', arity: 2, expression: 'x * sin(y)' },
  { token: 'sqrt', arity: 1, expression: 'sqrt(abs(x))' },
  { token: 'tan', arity: 2, expression: 'x * tan(y)' },
  { token: 'val', arity: 1, expression: 'x' },
]);

const DEFINITION_BY_TOKEN = new Map(
  FORMULA_DEFINITIONS.map((definition) => [definition.token, definition]),
);

export const FORMULA_NUMERIC_POLICY = Object.freeze({
  angleUnit: '60000ths-of-a-degree',
  intermediateRepresentation: 'IEEE-754 binary64',
  intermediateRounding: 'none',
  outputRounding: 'path-emitter-only',
  divisionByZero: 'evaluation-error',
  negativeSquareRoot: 'absolute-operand',
  nonFiniteResult: 'evaluation-error',
  atan2ZeroZero: 0,
});

export const FORMULA_COMPATIBILITY_NOTES = Object.freeze({
  sourceUrl:
    'https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oi29500/851cc3e8-ab22-4f5e-86d1-23b0b06d5edd',
  deviations: Object.freeze({
    angleUnit: 'PowerPoint measures trigonometric angles in 60000ths of a degree.',
    at2: 'PowerPoint uses atan2(y, x) and returns zero when both operands are zero.',
    mod: 'Office uses sqrt(x^2 + y^2 + z^2).',
    sqrt: 'Office uses sqrt(abs(x)).',
  }),
});

export const PREDEFINED_GUIDES = Object.freeze({
  '3cd4': 16200000,
  '3cd8': 8100000,
  '5cd8': 13500000,
  '7cd8': 18900000,
  b: 'input:height',
  cd2: 10800000,
  cd3: 7200000,
  cd4: 5400000,
  cd8: 2700000,
  h: 'input:height',
  hc: '*/ w 1 2',
  hd2: '*/ h 1 2',
  hd3: '*/ h 1 3',
  hd4: '*/ h 1 4',
  hd5: '*/ h 1 5',
  hd6: '*/ h 1 6',
  hd8: '*/ h 1 8',
  hd10: '*/ h 1 10',
  l: 0,
  ls: 'max w h',
  r: 'input:width',
  ss: 'min w h',
  ssd2: '*/ ss 1 2',
  ssd4: '*/ ss 1 4',
  ssd6: '*/ ss 1 6',
  ssd8: '*/ ss 1 8',
  ssd16: '*/ ss 1 16',
  ssd32: '*/ ss 1 32',
  t: 0,
  vc: '*/ h 1 2',
  w: 'input:width',
  wd2: '*/ w 1 2',
  wd3: '*/ w 1 3',
  wd4: '*/ w 1 4',
  wd5: '*/ w 1 5',
  wd6: '*/ w 1 6',
  wd8: '*/ w 1 8',
  wd10: '*/ w 1 10',
  wd12: '*/ w 1 12',
  wd32: '*/ w 1 32',
});

const NUMERIC_LITERAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;

/**
 * Parse and validate one `a:gd@fmla` value without evaluating it.
 *
 * @param {string} formula
 * @returns {{ operator: string, operands: Array<{kind: 'literal', value: number} | {kind: 'guide', name: string}> }}
 */
export function parseGuideFormula(formula) {
  const tokens = String(formula).trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) {
    throw new Error('Empty guide formula');
  }

  const [operator, ...rawOperands] = tokens;
  const definition = DEFINITION_BY_TOKEN.get(operator);
  if (!definition) {
    throw new Error(`Unknown guide formula operator: ${operator}`);
  }
  if (rawOperands.length !== definition.arity) {
    throw new Error(
      `Guide formula operator ${operator} expects ${definition.arity} operands; received ${rawOperands.length}`,
    );
  }

  const operands = rawOperands.map((operand) => {
    if (!NUMERIC_LITERAL.test(operand)) {
      return { kind: 'guide', name: operand };
    }
    const value = Number(operand);
    if (!Number.isFinite(value)) {
      throw new Error(`Guide formula requires a finite numeric literal: ${operand}`);
    }
    return { kind: 'literal', value };
  });

  return { operator, operands };
}
