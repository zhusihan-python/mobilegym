import { describe, expect, it } from 'vitest';

import type { AppManifest } from '../os/types/manifest';
import { buildSimMetadata } from '../os/simMetadata';

const manifests = [
  {
    id: 'wechat',
    packageName: 'com.tencent.mm',
    displayName: '微信',
    displayNameEn: 'WeChat',
    version: '8.0.46',
    versionCode: 80046,
    type: 'plugin',
  },
  {
    id: 'settings',
    packageName: 'com.android.settings',
    displayName: '设置',
    displayNameEn: 'Settings',
    version: '14.0.0',
    versionCode: 140000,
    type: 'system',
  },
] as AppManifest[];

describe('simulator metadata contract', () => {
  it('includes package and version metadata for every installed manifest', () => {
    const metadata = buildSimMetadata(manifests, {
      version: '0.1.0',
      buildId: 'build-vs02',
      sourceRevision: 'abc123',
      bundleHash: 'bundle-sha',
      dataRevision: 'seed-v1',
      dataBundleHash: 'data-sha',
    });

    expect(metadata.schemaVersion).toBe(1);
    expect(metadata.simulator).toEqual({
      product: 'mobile-gym',
      version: '0.1.0',
      buildId: 'build-vs02',
      sourceRevision: 'abc123',
      bundleHash: 'bundle-sha',
    });
    expect(metadata.data).toEqual({
      revision: 'seed-v1',
      bundleHash: 'data-sha',
    });
    expect(metadata.apps).toEqual([
      {
        id: 'settings',
        packageName: 'com.android.settings',
        displayName: '设置',
        displayNameEn: 'Settings',
        version: '14.0.0',
        versionCode: 140000,
        type: 'system',
      },
      {
        id: 'wechat',
        packageName: 'com.tencent.mm',
        displayName: '微信',
        displayNameEn: 'WeChat',
        version: '8.0.46',
        versionCode: 80046,
        type: 'plugin',
      },
    ]);
    expect(metadata.capabilities).toContain('sim.metadata.v1');
  });

  it('is deterministic for the same build inputs regardless of manifest order', () => {
    const build = {
      version: '0.1.0',
      buildId: 'build-vs02',
      sourceRevision: 'abc123',
      bundleHash: 'bundle-sha',
      dataRevision: null,
      dataBundleHash: null,
    };

    expect(buildSimMetadata(manifests, build)).toEqual(
      buildSimMetadata([...manifests].reverse(), build),
    );
  });
});
