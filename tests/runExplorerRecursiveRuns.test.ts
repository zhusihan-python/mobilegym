import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import {
  discoverRunsExplorerRuns,
  resolveRunsExplorerRunRequest,
} from '../vite.config';

const tmpRoots: string[] = [];

function tmpRunsDir() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mg-runs-explorer-'));
  tmpRoots.push(root);
  return root;
}

function makeRun(root: string, rel: string) {
  const dir = path.join(root, rel);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'meta.json'), '{}\n', 'utf8');
  fs.mkdirSync(path.join(dir, 'trajectory'), { recursive: true });
  return dir;
}

describe('Run Explorer recursive run discovery', () => {
  afterEach(() => {
    for (const root of tmpRoots.splice(0)) {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it('discovers flat, legacy nested, and platform lane-attempt run roots', () => {
    const runsDir = tmpRunsDir();
    makeRun(runsDir, 'flat-cli');
    makeRun(runsDir, 'agent-a/20260706_010101');
    makeRun(runsDir, 'platform-run/lanes/candidate/attempts/0001');
    fs.mkdirSync(path.join(runsDir, 'not-a-run/deep'), { recursive: true });

    expect(new Set(discoverRunsExplorerRuns(runsDir))).toEqual(new Set([
      'flat-cli',
      'agent-a/20260706_010101',
      'platform-run/lanes/candidate/attempts/0001',
    ]));
  });

  it('resolves the deepest matching run root before treating the rest as a subpath', () => {
    const runsDir = tmpRunsDir();
    const runDir = makeRun(runsDir, 'platform-run/lanes/candidate/attempts/0001');

    const resolved = resolveRunsExplorerRunRequest(runsDir, [
      'platform-run',
      'lanes',
      'candidate',
      'attempts',
      '0001',
      'trajectory',
    ]);

    expect(resolved).toEqual({
      runName: 'platform-run/lanes/candidate/attempts/0001',
      runDir,
      runDepth: 5,
      subPath: 'trajectory',
    });
  });

  it('rejects traversal path segments', () => {
    const runsDir = tmpRunsDir();
    makeRun(runsDir, 'flat-cli');

    expect(resolveRunsExplorerRunRequest(runsDir, ['..', 'flat-cli'])).toBeNull();
  });
});
