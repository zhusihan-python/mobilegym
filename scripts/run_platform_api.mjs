import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const candidates = [
  resolve(repoRoot, '.venv/bin/python'),
  resolve(repoRoot, '.venv/Scripts/python.exe'),
  'python',
  'python3',
];

for (const candidate of candidates) {
  if (candidate.includes('/') && !existsSync(candidate)) continue;
  const probe = spawnSync(candidate, ['-c', 'import fastapi, uvicorn'], {
    cwd: repoRoot,
    stdio: 'ignore',
  });
  if (probe.error?.code === 'ENOENT' || probe.status !== 0) continue;

  const result = spawnSync(candidate, ['-m', 'test_platform.main'], {
    cwd: repoRoot,
    stdio: 'inherit',
    env: process.env,
  });
  if (result.error) {
    console.error(`[platform:api] failed to launch ${candidate}: ${result.error.message}`);
    process.exit(1);
  }
  if (result.signal) {
    process.kill(process.pid, result.signal);
  }
  process.exit(result.status ?? 1);
}

console.error('[platform:api] no Python runtime with fastapi and uvicorn was found.');
console.error('[platform:api] create/activate .venv, then install the test platform Python dependencies.');
process.exit(1);
