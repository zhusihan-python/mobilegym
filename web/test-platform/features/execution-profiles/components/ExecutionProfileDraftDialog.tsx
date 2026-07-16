import { useState, type FormEvent } from 'react';

import {
  createExecutionProfile,
  updateExecutionProfileDraft,
} from '../../../api/client';
import type {
  ExecutionProfile,
  ExecutionProfileSpec,
} from '../../../api/types';

export function ExecutionProfileDraftDialog({
  projectId,
  profile,
  onSaved,
  onCancel,
}: {
  projectId: string;
  profile?: ExecutionProfile;
  onSaved: (profile: ExecutionProfile) => void;
  onCancel: () => void;
}) {
  const initialSpec = profile?.draft_spec;
  const [name, setName] = useState(profile?.name ?? 'Generic v2 / local model');
  const [modelBaseUrl, setModelBaseUrl] = useState(initialSpec?.model.base_url ?? 'http://127.0.0.1:1234/v1');
  const [modelName, setModelName] = useState(initialSpec?.model.name ?? 'local-model');
  const [imageFormat, setImageFormat] = useState<'data_url' | 'bare_base64'>(initialSpec?.image_input.format ?? 'data_url');
  const [temperature, setTemperature] = useState(String(initialSpec?.generation.temperature ?? 0));
  const [topP, setTopP] = useState(String(initialSpec?.generation.top_p ?? 1));
  const [maxTokens, setMaxTokens] = useState(String(initialSpec?.generation.max_tokens ?? 4096));
  const [stream, setStream] = useState(initialSpec?.generation.stream ?? true);
  const [timeoutSeconds, setTimeoutSeconds] = useState(String(initialSpec?.inference.timeout_seconds ?? 300));
  const [requireModelCredential, setRequireModelCredential] = useState(
    initialSpec?.credentials.required_slots.includes('model_api_key') ?? false,
  );
  const [credentialReferenceId, setCredentialReferenceId] = useState(
    profile ? '' : 'primary-model-key',
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const draftSpec: ExecutionProfileSpec = {
      schema_version: 1,
      agent: { id: 'generic_v2' },
      model: {
        protocol: 'openai_chat_completions',
        base_url: modelBaseUrl.trim(),
        name: modelName.trim(),
      },
      image_input: { format: imageFormat },
      generation: {
        temperature: Number(temperature),
        top_p: Number(topP),
        max_tokens: Number(maxTokens),
        stream,
      },
      inference: { timeout_seconds: Number(timeoutSeconds) },
      credentials: {
        required_slots: requireModelCredential ? ['model_api_key'] : [],
      },
    };
    setSubmitting(true);
    setError(null);
    const credentialBinding = {
      slot: 'model_api_key' as const,
      project_id: projectId,
      backend: 'request' as const,
      reference_id: credentialReferenceId.trim(),
      private_locator: 'request://transient/model-api-key',
    };
    const request = profile
      ? updateExecutionProfileDraft({
        projectId,
        executionProfileId: profile.id,
        name: name.trim(),
        draftSpec,
        expectedDraftVersion: profile.draft_version,
        credentialBindings: !requireModelCredential
          ? []
          : credentialReferenceId.trim()
            ? [credentialBinding]
            : undefined,
      })
      : createExecutionProfile({
        projectId,
        name: name.trim(),
        draftSpec,
        credentialBindings: requireModelCredential ? [credentialBinding] : [],
      });
    request
      .then(onSaved)
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Profile could not be created.');
      })
      .finally(() => setSubmitting(false));
  };

  return (
    <div className="tp-modal-backdrop">
      <form className="tp-modal tp-profile-modal" role="dialog" aria-modal="true" aria-label={profile ? 'Edit Execution Profile' : 'Create Execution Profile'} onSubmit={submit}>
        <h2>{profile ? 'Edit Execution Profile draft' : 'New Execution Profile'}</h2>
        <label>Profile name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Agent<select value="generic_v2" disabled><option value="generic_v2">generic_v2</option></select></label>
        <label>Model endpoint<input value={modelBaseUrl} onChange={(event) => setModelBaseUrl(event.target.value)} /></label>
        <label>Model name<input value={modelName} onChange={(event) => setModelName(event.target.value)} /></label>
        <label>Image Input Format<select value={imageFormat} onChange={(event) => setImageFormat(event.target.value as 'data_url' | 'bare_base64')}><option value="data_url">data_url</option><option value="bare_base64">bare_base64</option></select></label>
        <label>Temperature<input type="number" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.value)} /></label>
        <label>Top p<input type="number" step="0.1" value={topP} onChange={(event) => setTopP(event.target.value)} /></label>
        <label>Max tokens<input type="number" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} /></label>
        <label className="tp-checkbox-field"><input type="checkbox" checked={stream} onChange={(event) => setStream(event.target.checked)} />Streaming</label>
        <label>Inference timeout<input type="number" value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(event.target.value)} /></label>
        <label className="tp-checkbox-field">
          <input
            type="checkbox"
            checked={requireModelCredential}
            onChange={(event) => setRequireModelCredential(event.target.checked)}
          />
          Require model credential
        </label>
        {requireModelCredential ? (
          <label>
            Credential reference ID
            <input
              value={credentialReferenceId}
              onChange={(event) => setCredentialReferenceId(event.target.value)}
            />
          </label>
        ) : null}
        {error ? <div className="tp-inline-alert" role="alert">{error}</div> : null}
        <div className="tp-modal-actions">
          <button type="button" onClick={onCancel}>Cancel</button>
          <button
            type="submit"
            disabled={
              submitting
              || (!profile && requireModelCredential && !credentialReferenceId.trim())
            }
          >
            {submitting ? 'Saving...' : profile ? 'Save draft' : 'Create profile'}
          </button>
        </div>
      </form>
    </div>
  );
}
