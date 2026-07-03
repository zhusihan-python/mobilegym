import { describe, expect, it } from 'vitest';

import type { AppManifest } from '../os/types/manifest';
import { buildInstalledAppsState } from '../os/simMetadata';

describe('simulator state installed apps metadata', () => {
  it('exposes package name and version fields in the installed apps snapshot', () => {
    const apps = buildInstalledAppsState([
      {
        id: 'settings',
        packageName: 'com.android.settings',
        displayName: '设置',
        displayNameEn: 'Settings',
        version: '14.0.0',
        versionCode: 140000,
        type: 'system',
      },
    ] as AppManifest[], (key) => key);

    expect(apps).toEqual([
      {
        id: 'settings',
        name: '设置',
        packageName: 'com.android.settings',
        type: 'system',
        version: '14.0.0',
        versionCode: 140000,
      },
    ]);
  });
});
