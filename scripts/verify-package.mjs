import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createRequire } from 'node:module';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, posix, resolve } from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

import { getNpmPackInvocation, getPackageAuditManifest } from './package-contract.mjs';

const require = createRequire(import.meta.url);
const execFileAsync = promisify(execFile);
const repositoryRoot = fileURLToPath(new URL('..', import.meta.url));
const packageJson = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
const browserEntry = new URL(
  `..${packageJson.exports['./browser'].import.slice(1)}`,
  import.meta.url,
);
const browserSource = await readFile(browserEntry, 'utf8');

assert.equal(browserSource.includes('process.env'), false, 'browser entry contains process.env');
for (const dependency of ['echarts', 'jszip']) {
  assert.equal(
    browserSource.includes(`from "${dependency}"`) ||
      browserSource.includes(`from '${dependency}'`) ||
      browserSource.includes(`require("${dependency}")`) ||
      browserSource.includes(`require('${dependency}')`),
    false,
    `browser entry contains a bare ${dependency} import`,
  );
}

const esm = await import(packageJson.name);
const browser = await import(`${packageJson.name}/browser`);
const cjs = require(packageJson.name);

for (const [name, entry] of Object.entries({ esm, browser, cjs })) {
  assert.equal(typeof entry.PptxViewer, 'function', `${name} entry does not export PptxViewer`);
  assert.equal(typeof entry.parseZip, 'function', `${name} entry does not export parseZip`);
}

const npmCache = await mkdtemp(join(tmpdir(), 'pptx-renderer-npm-cache-'));
const packageStaging = await mkdtemp(join(tmpdir(), 'pptx-renderer-pack-audit-'));
let packOutput;
try {
  const auditInputs = [
    'dist',
    'README.md',
    'CHANGELOG.md',
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    'licenses',
    'scripts/ooxml-geometry',
    'scripts/package-contract.mjs',
  ];
  await Promise.all(
    auditInputs.map(async (path) => {
      const destination = resolve(packageStaging, path);
      await mkdir(dirname(destination), { recursive: true });
      await cp(resolve(repositoryRoot, path), destination, { recursive: true });
    }),
  );
  await writeFile(
    resolve(packageStaging, 'package.json'),
    `${JSON.stringify(getPackageAuditManifest(packageJson), null, 2)}\n`,
    'utf8',
  );
  const invocation = getNpmPackInvocation({ cachePath: npmCache });
  ({ stdout: packOutput } = await execFileAsync(invocation.executable, invocation.arguments, {
    cwd: packageStaging,
    maxBuffer: 10 * 1024 * 1024,
  }));
} finally {
  await Promise.all([
    rm(npmCache, { recursive: true, force: true }),
    rm(packageStaging, { recursive: true, force: true }),
  ]);
}
const packReports = JSON.parse(packOutput);
assert.equal(packReports.length, 1, 'npm pack did not return exactly one package report');
const packedPaths = new Set(packReports[0].files.map(({ path }) => path.replaceAll('\\', '/')));

for (const requiredPath of [
  'THIRD_PARTY_NOTICES.md',
  'licenses/ECMA-text-copyright-notice.txt',
  'scripts/ooxml-geometry/source-manifest.json',
]) {
  assert.equal(packedPaths.has(requiredPath), true, `package is missing required ${requiredPath}`);
}

for (const excludedPath of [
  'scripts/ooxml-geometry/vendor/OfficeOpenXML-DrawingMLGeometries.zip',
  'scripts/ooxml-geometry/generated/preset-shape-catalog.json',
  'scripts/ooxml-geometry/formula-contract.mjs',
  'scripts/ooxml-geometry/formula-evaluator.mjs',
  'scripts/ooxml-geometry/generate.mjs',
  'scripts/ooxml-geometry/geometry-ir.mjs',
  'scripts/ooxml-geometry/path-emitter.mjs',
  'scripts/ooxml-geometry/source-reconciliation.json',
  'scripts/ooxml-geometry/source-validator.mjs',
  'scripts/package-contract.mjs',
]) {
  assert.equal(
    packedPaths.has(excludedPath),
    false,
    `package unexpectedly includes ${excludedPath}`,
  );
}

const noticePath = 'THIRD_PARTY_NOTICES.md';
const notice = await readFile(new URL(`../${noticePath}`, import.meta.url), 'utf8');
const relativeLinks = [...notice.matchAll(/\]\(([^)]+)\)/g)]
  .map(([, target]) => target.split('#', 1)[0])
  .filter((target) => target && !/^[a-z][a-z\d+.-]*:/i.test(target) && !target.startsWith('#'));
for (const target of relativeLinks) {
  const resolved = posix.normalize(posix.join(dirname(noticePath), decodeURIComponent(target)));
  assert.equal(
    resolved.startsWith('../'),
    false,
    `third-party notice link escapes the package: ${target}`,
  );
  assert.equal(packedPaths.has(resolved), true, `third-party notice link is not packed: ${target}`);
}

console.log(
  'Verified ESM, CJS, standalone browser entry points, package contents, and notice links.',
);
