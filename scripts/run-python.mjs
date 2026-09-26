import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const forwarded = process.argv.slice(2);
if (forwarded.length === 0) {
  console.error('Usage: node scripts/run-python.mjs <script> [args...]');
  process.exit(2);
}

const candidates = [];
if (process.env.PYTHON) candidates.push([process.env.PYTHON]);
if (process.platform === 'win32') {
  candidates.push([resolve('test/e2e/.venv/Scripts/python.exe')]);
  candidates.push(['py', '-3'], ['python'], ['python3']);
} else {
  candidates.push([resolve('test/e2e/.venv/bin/python')]);
  candidates.push(['python3'], ['python']);
}

for (const [executable, ...prefix] of candidates) {
  const result = spawnSync(executable, [...prefix, ...forwarded], {
    stdio: 'inherit',
    env: process.env,
  });
  if (result.error?.code === 'ENOENT') continue;
  if (result.error) {
    console.error(`Unable to launch ${executable}: ${result.error.message}`);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

console.error('Python 3 was not found. Set PYTHON to an executable path.');
process.exit(127);
