import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';

import { listExecutionProfiles } from '../../api/client';
import type {
  CollectionResponse,
  ExecutionProfile,
  ExecutionProfileRevision,
  Project,
} from '../../api/types';
import { ExecutionProfileCard } from './components/ExecutionProfileCard';
import { ExecutionProfileDraftDialog } from './components/ExecutionProfileDraftDialog';
import {
  executionProfileDraftFromLegacyPreferences,
  type LegacyLaunchPreferences,
} from './legacyLaunchPreferences';

type ProfilesState =
  | { status: 'loading' }
  | { status: 'loaded'; data: CollectionResponse<ExecutionProfile> }
  | { status: 'error'; message: string };

export function ExecutionProfilesPage() {
  const {
    selectedProject,
    legacyLaunchPreferences,
    onLegacyLaunchPreferencesConsumed,
  } = useOutletContext<{
    selectedProject: Project;
    legacyLaunchPreferences: LegacyLaunchPreferences | null;
    onLegacyLaunchPreferencesConsumed: () => void;
  }>();
  const [profiles, setProfiles] = useState<ProfilesState>({ status: 'loading' });
  const [createOpen, setCreateOpen] = useState(false);
  const [legacyDraftOpen, setLegacyDraftOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const legacyDraft = legacyLaunchPreferences
    ? executionProfileDraftFromLegacyPreferences(legacyLaunchPreferences)
    : null;

  const loadProfiles = () => {
    setProfiles({ status: 'loading' });
    listExecutionProfiles(selectedProject.id, showArchived)
      .then((data) => setProfiles({ status: 'loaded', data }))
      .catch((error) => {
        setProfiles({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to load Execution Profiles.',
        });
      });
  };

  useEffect(() => {
    loadProfiles();
  }, [selectedProject.id, showArchived]);

  const handleCreated = (profile: ExecutionProfile) => {
    setProfiles((current) => {
      if (current.status !== 'loaded') return current;
      return {
        status: 'loaded',
        data: { ...current.data, items: [...current.data.items, profile] },
      };
    });
    setCreateOpen(false);
  };

  const handleLegacyDraftCreated = (profile: ExecutionProfile) => {
    handleCreated(profile);
    setLegacyDraftOpen(false);
    onLegacyLaunchPreferencesConsumed();
  };

  const handlePublished = (
    executionProfileId: string,
    revision: ExecutionProfileRevision,
  ) => {
    setProfiles((current) => {
      if (current.status !== 'loaded') return current;
      return {
        status: 'loaded',
        data: {
          ...current.data,
          items: current.data.items.map((profile) =>
            profile.id === executionProfileId
              ? { ...profile, head_revision: revision }
              : profile,
          ),
        },
      };
    });
  };

  const handleProfileChanged = (changed: ExecutionProfile) => {
    setProfiles((current) => {
      if (current.status !== 'loaded') return current;
      const exists = current.data.items.some((profile) => profile.id === changed.id);
      return {
        status: 'loaded',
        data: {
          ...current.data,
          items: exists
            ? current.data.items
              .map((profile) => (profile.id === changed.id ? changed : profile))
              .filter((profile) => showArchived || profile.archived_at === null)
            : [...current.data.items, changed],
        },
      };
    });
  };

  if (profiles.status === 'loading') {
    return <section className="tp-panel">Loading Execution Profiles...</section>;
  }
  if (profiles.status === 'error') {
    return (
      <section className="tp-alert" role="alert">
        <h2>Execution Profiles could not be loaded</h2>
        <p>{profiles.message}</p>
        <button type="button" onClick={loadProfiles}>Retry profiles</button>
      </section>
    );
  }

  return (
    <>
      <section className="tp-panel tp-page-actions">
        <div>
          <h2>Execution Profiles</h2>
          <p>Immutable Agent and model subject identity for {selectedProject.name}.</p>
        </div>
        <div className="tp-page-actions">
          <label className="tp-checkbox-field">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(event) => setShowArchived(event.target.checked)}
            />
            Show archived profiles
          </label>
          <button type="button" onClick={() => setCreateOpen(true)}>
            New execution profile
          </button>
          {legacyLaunchPreferences ? (
            <button type="button" onClick={() => setLegacyDraftOpen(true)}>
              Review saved launch preferences
            </button>
          ) : null}
        </div>
      </section>

      {profiles.data.items.length === 0 ? (
        <section className="tp-empty">
          <h2>No Execution Profiles yet</h2>
          <p>Create a no-secret subject draft, review it, and publish revision 1.</p>
          <button type="button" onClick={() => setCreateOpen(true)}>
            Create first execution profile
          </button>
        </section>
      ) : (
        <section className="tp-target-list" aria-label="Execution Profiles">
          {profiles.data.items.map((profile) => (
            <ExecutionProfileCard
              key={profile.id}
              projectId={selectedProject.id}
              profile={profile}
              onPublished={handlePublished}
              onChanged={handleProfileChanged}
            />
          ))}
        </section>
      )}

      {createOpen ? (
        <ExecutionProfileDraftDialog
          projectId={selectedProject.id}
          onSaved={handleCreated}
          onCancel={() => setCreateOpen(false)}
        />
      ) : null}
      {legacyDraftOpen && legacyDraft ? (
        <ExecutionProfileDraftDialog
          projectId={selectedProject.id}
          initialName={legacyDraft.name}
          initialSpec={legacyDraft.spec}
          onSaved={handleLegacyDraftCreated}
          onCancel={() => setLegacyDraftOpen(false)}
        />
      ) : null}
    </>
  );
}
