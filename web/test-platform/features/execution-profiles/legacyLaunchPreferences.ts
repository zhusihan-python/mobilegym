import type { ExecutionProfileSpec } from '../../api/types';

const DEPRECATED_LAUNCH_STORAGE_PREFIX = 'test-platform.launch.';
const AGENT_STORAGE_KEY = `${DEPRECATED_LAUNCH_STORAGE_PREFIX}agent`;
const MODEL_BASE_URL_STORAGE_KEY = `${DEPRECATED_LAUNCH_STORAGE_PREFIX}model-base-url`;
const MODEL_NAME_STORAGE_KEY = `${DEPRECATED_LAUNCH_STORAGE_PREFIX}model-name`;
const IMAGE_URL_FORMAT_STORAGE_KEY = `${DEPRECATED_LAUNCH_STORAGE_PREFIX}image-url-format`;

export type LegacyLaunchPreferences = {
  modelBaseUrl: string;
  modelName: string;
  imageInputFormat: 'data_url' | 'bare_base64';
};

export function takeLegacyLaunchPreferences(
  storage: Storage,
): LegacyLaunchPreferences | null {
  const agent = (storage.getItem(AGENT_STORAGE_KEY) ?? 'generic_v2').trim();
  const modelBaseUrl = (storage.getItem(MODEL_BASE_URL_STORAGE_KEY) ?? '').trim();
  const modelName = (storage.getItem(MODEL_NAME_STORAGE_KEY) ?? '').trim();
  const storedImageFormat = storage.getItem(IMAGE_URL_FORMAT_STORAGE_KEY);
  const imageInputFormat = storedImageFormat === 'bare_base64'
    ? 'bare_base64'
    : 'data_url';

  Array.from({ length: storage.length }, (_, index) => storage.key(index))
    .filter((key): key is string => Boolean(key?.startsWith(DEPRECATED_LAUNCH_STORAGE_PREFIX)))
    .forEach((key) => storage.removeItem(key));

  if (agent !== 'generic_v2' || !modelBaseUrl || !modelName) {
    return null;
  }
  return { modelBaseUrl, modelName, imageInputFormat };
}

export function executionProfileDraftFromLegacyPreferences(
  preferences: LegacyLaunchPreferences,
): { name: string; spec: ExecutionProfileSpec } {
  return {
    name: `Imported ${preferences.modelName}`,
    spec: {
      schema_version: 1,
      agent: { id: 'generic_v2' },
      model: {
        protocol: 'openai_chat_completions',
        base_url: preferences.modelBaseUrl,
        name: preferences.modelName,
      },
      image_input: { format: preferences.imageInputFormat },
      generation: {
        temperature: 0,
        top_p: 1,
        max_tokens: 4096,
        stream: true,
      },
      inference: { timeout_seconds: 300 },
      credentials: { required_slots: [] },
    },
  };
}
