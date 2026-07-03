import packageInfo from '../package.json';
import type { AppManifest } from './types/manifest';

export type SimMetadataApp = {
  id: string;
  packageName: string;
  displayName: string;
  displayNameEn?: string;
  version: string;
  versionCode: number;
  type: AppManifest['type'];
};

export type SimMetadata = {
  schemaVersion: 1;
  simulator: {
    product: 'mobile-gym';
    version: string;
    buildId: string;
    sourceRevision?: string;
    bundleHash?: string;
  };
  apps: SimMetadataApp[];
  data: {
    revision: string | null;
    bundleHash: string | null;
  };
  capabilities: string[];
};

export type SimBuildInfo = {
  version?: string;
  buildId?: string | null;
  sourceRevision?: string | null;
  bundleHash?: string | null;
  dataRevision?: string | null;
  dataBundleHash?: string | null;
};

export type InstalledAppState = {
  id: string;
  name: string;
  packageName: string;
  type: AppManifest['type'];
  version: string;
  versionCode: number;
};

export function buildSimMetadata(
  manifests: readonly AppManifest[],
  build: SimBuildInfo = readBuildInfoFromEnv(),
): SimMetadata {
  const simulator: SimMetadata['simulator'] = {
    product: 'mobile-gym',
    version: valueOrDefault(build.version, packageInfo.version),
    buildId: valueOrDefault(build.buildId, 'dev-unversioned'),
  };

  const sourceRevision = cleanOptional(build.sourceRevision);
  if (sourceRevision) simulator.sourceRevision = sourceRevision;
  const bundleHash = cleanOptional(build.bundleHash);
  if (bundleHash) simulator.bundleHash = bundleHash;

  return {
    schemaVersion: 1,
    simulator,
    apps: normalizeApps(manifests),
    data: {
      revision: cleanOptional(build.dataRevision),
      bundleHash: cleanOptional(build.dataBundleHash),
    },
    capabilities: [
      'sim.metadata.v1',
      'sim.state.v1',
    ],
  };
}

export function buildInstalledAppsState(
  manifests: readonly AppManifest[],
  translate: (key: string) => string,
): InstalledAppState[] {
  return normalizeApps(manifests).map((app) => ({
    id: app.id,
    name: translate(app.displayName),
    packageName: app.packageName,
    type: app.type,
    version: app.version,
    versionCode: app.versionCode,
  }));
}

function normalizeApps(manifests: readonly AppManifest[]): SimMetadataApp[] {
  return manifests
    .map((manifest) => ({
      id: manifest.id,
      packageName: manifest.packageName,
      displayName: manifest.displayName,
      displayNameEn: manifest.displayNameEn,
      version: manifest.version,
      versionCode: manifest.versionCode,
      type: manifest.type,
    }))
    .sort((left, right) => left.id.localeCompare(right.id));
}

function readBuildInfoFromEnv(): SimBuildInfo {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  return {
    version: packageInfo.version,
    buildId: env.VITE_MOBILEGYM_BUILD_ID,
    sourceRevision: env.VITE_MOBILEGYM_SOURCE_REVISION,
    bundleHash: env.VITE_MOBILEGYM_BUNDLE_HASH,
    dataRevision: env.VITE_MOBILEGYM_DATA_REVISION,
    dataBundleHash: env.VITE_MOBILEGYM_DATA_BUNDLE_HASH,
  };
}

function cleanOptional(value: string | null | undefined): string | null {
  if (value == null) return null;
  const cleaned = String(value).trim();
  return cleaned ? cleaned : null;
}

function valueOrDefault(value: string | null | undefined, fallback: string): string {
  return cleanOptional(value) ?? fallback;
}
