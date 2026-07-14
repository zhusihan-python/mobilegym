import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

import { archiveProject, createProject, fetchReadiness, listProjects } from '../api/client';
import type { Project, ReadinessResponse } from '../api/types';

const SELECTED_PROJECT_STORAGE_KEY = 'test-platform.selected-project-id';

type ReadinessState =
  | { status: 'loading' }
  | { status: 'ready'; data: ReadinessResponse }
  | { status: 'not-ready'; data: ReadinessResponse }
  | { status: 'error'; message: string };

type ProjectsState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'loaded'; projects: Project[]; selectedProjectId: string | null }
  | { status: 'error'; message: string };

export function PlatformShell() {
  const location = useLocation();
  const [readiness, setReadiness] = useState<ReadinessState>({ status: 'loading' });
  const [projectsState, setProjectsState] = useState<ProjectsState>({ status: 'idle' });
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  const loadReadiness = useCallback(() => {
    setReadiness({ status: 'loading' });
    fetchReadiness()
      .then((data) => {
        setReadiness(data.ready ? { status: 'ready', data } : { status: 'not-ready', data });
      })
      .catch((error) => {
        setReadiness({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to reach the Test Platform API.',
        });
      });
  }, []);

  useEffect(() => {
    loadReadiness();
  }, [loadReadiness]);

  const ready = readiness.status === 'ready';

  const loadProjects = useCallback(() => {
    setProjectsState({ status: 'loading' });
    listProjects()
      .then((response) => {
        const projects = response.items;
        const storedProjectId = window.localStorage.getItem(SELECTED_PROJECT_STORAGE_KEY);
        const selectedProjectId = resolveSelectedProjectId(projects, storedProjectId);
        if (selectedProjectId) {
          window.localStorage.setItem(SELECTED_PROJECT_STORAGE_KEY, selectedProjectId);
        } else {
          window.localStorage.removeItem(SELECTED_PROJECT_STORAGE_KEY);
        }
        setProjectsState({ status: 'loaded', projects, selectedProjectId });
      })
      .catch((error) => {
        setProjectsState({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to load projects.',
        });
      });
  }, []);

  useEffect(() => {
    if (ready) {
      loadProjects();
    } else {
      setProjectsState({ status: 'idle' });
    }
  }, [loadProjects, ready]);

  const selectedProject =
    projectsState.status === 'loaded'
      ? projectsState.projects.find((project) => project.id === projectsState.selectedProjectId) ?? null
      : null;

  const selectProject = (projectId: string) => {
    if (projectsState.status !== 'loaded') {
      return;
    }
    window.localStorage.setItem(SELECTED_PROJECT_STORAGE_KEY, projectId);
    setProjectsState({ ...projectsState, selectedProjectId: projectId });
  };

  const handleProjectCreated = (project: Project) => {
    window.localStorage.setItem(SELECTED_PROJECT_STORAGE_KEY, project.id);
    setProjectsState((current) => {
      if (current.status !== 'loaded') {
        return current;
      }
      return {
        status: 'loaded',
        projects: [...current.projects, project],
        selectedProjectId: project.id,
      };
    });
    setCreateDialogOpen(false);
  };

  const archiveSelectedProject = () => {
    if (projectsState.status !== 'loaded' || !projectsState.selectedProjectId) {
      return;
    }
    archiveProject(projectsState.selectedProjectId)
      .then((archivedProject) => {
        setProjectsState((current) => {
          if (current.status !== 'loaded') {
            return current;
          }
          const projects = current.projects.filter((project) => project.id !== archivedProject.id);
          const selectedProjectId = resolveSelectedProjectId(projects, current.selectedProjectId);
          if (selectedProjectId) {
            window.localStorage.setItem(SELECTED_PROJECT_STORAGE_KEY, selectedProjectId);
          } else {
            window.localStorage.removeItem(SELECTED_PROJECT_STORAGE_KEY);
          }
          return { status: 'loaded', projects, selectedProjectId };
        });
      })
      .catch(() => {
        loadProjects();
      });
  };

  return (
    <div className="tp-root">
      <aside className="tp-sidebar">
        <div>
          <div className="tp-brand">MobileGym</div>
          <div className="tp-subtitle">Test Platform</div>
        </div>
        {ready ? (
          <ProjectSwitcher
            projectsState={projectsState}
            selectedProjectId={projectsState.status === 'loaded' ? projectsState.selectedProjectId : null}
            onSelect={selectProject}
            onNewProject={() => setCreateDialogOpen(true)}
            onArchiveProject={archiveSelectedProject}
          />
        ) : null}
        <nav className="tp-nav" aria-label="Test Platform">
          <NavLink to="/runs">Runs</NavLink>
          <NavLink to="/baselines">Baselines</NavLink>
          <NavLink to="/tasks">Tasks</NavLink>
          <NavLink to="/targets">Targets</NavLink>
          <NavLink to="/workflows">Workflows</NavLink>
          <NavLink to="/execution-profiles">Execution Profiles</NavLink>
        </nav>
      </aside>

      <div className="tp-main">
        <header className="tp-topbar">
          <div>
            <div className="tp-kicker">Local console</div>
            <h1>{pageTitle(location.pathname)}</h1>
          </div>
          <ReadinessIndicator readiness={readiness} />
        </header>

        {ready ? (
          <WorkspaceBody
            projectsState={projectsState}
            selectedProject={selectedProject}
            onNewProject={() => setCreateDialogOpen(true)}
          />
        ) : (
          <ReadinessPanel readiness={readiness} onRetry={loadReadiness} />
        )}
      </div>

      {createDialogOpen && projectsState.status === 'loaded' ? (
        <CreateProjectDialog
          projects={projectsState.projects}
          onCreated={handleProjectCreated}
          onCancel={() => setCreateDialogOpen(false)}
        />
      ) : null}
    </div>
  );
}

function WorkspaceBody({
  projectsState,
  selectedProject,
  onNewProject,
}: {
  projectsState: ProjectsState;
  selectedProject: Project | null;
  onNewProject: () => void;
}) {
  if (projectsState.status === 'idle' || projectsState.status === 'loading') {
    return <section className="tp-panel">Loading projects...</section>;
  }

  if (projectsState.status === 'error') {
    return (
      <section className="tp-alert" role="alert">
        <h2>Projects could not be loaded</h2>
        <p>{projectsState.message}</p>
      </section>
    );
  }

  if (!selectedProject) {
    return (
      <section className="tp-empty">
        <h2>Create a project to start</h2>
        <p>Projects group targets, workflows, runs, and reports.</p>
        <button type="button" onClick={onNewProject}>
          New project
        </button>
      </section>
    );
  }

  return <Outlet context={{ selectedProject }} />;
}

function ProjectSwitcher({
  projectsState,
  selectedProjectId,
  onSelect,
  onNewProject,
  onArchiveProject,
}: {
  projectsState: ProjectsState;
  selectedProjectId: string | null;
  onSelect: (projectId: string) => void;
  onNewProject: () => void;
  onArchiveProject: () => void;
}) {
  if (projectsState.status === 'idle' || projectsState.status === 'loading') {
    return <div className="tp-project-switcher">Loading projects...</div>;
  }

  if (projectsState.status === 'error') {
    return <div className="tp-project-switcher">Projects unavailable</div>;
  }

  return (
    <div className="tp-project-switcher">
      <label htmlFor="tp-project-select">Project</label>
      <select
        id="tp-project-select"
        value={selectedProjectId ?? ''}
        onChange={(event) => onSelect(event.target.value)}
        disabled={projectsState.projects.length === 0}
      >
        {projectsState.projects.length === 0 ? (
          <option value="">No projects</option>
        ) : (
          projectsState.projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))
        )}
      </select>
      <button type="button" onClick={onNewProject}>
        New project
      </button>
      <button type="button" onClick={onArchiveProject} disabled={!selectedProjectId}>
        Archive project
      </button>
    </div>
  );
}

function CreateProjectDialog({
  projects,
  onCreated,
  onCancel,
}: {
  projects: Project[];
  onCreated: (project: Project) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanedName = normalizeProjectNameForDisplay(name);
    if (!cleanedName) {
      setError('Project name is required.');
      return;
    }
    if (projects.some((project) => normalizeProjectName(project.name) === normalizeProjectName(cleanedName))) {
      setError('A project with this name already exists.');
      return;
    }

    setError(null);
    setSubmitting(true);
    createProject(cleanedName)
      .then(onCreated)
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Project could not be created.');
      })
      .finally(() => {
        setSubmitting(false);
      });
  };

  return (
    <div className="tp-modal-backdrop">
      <form className="tp-modal" role="dialog" aria-modal="true" aria-label="Create project" onSubmit={submit}>
        <h2>New project</h2>
        <label htmlFor="tp-project-name">Project name</label>
        <input
          id="tp-project-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          autoFocus
        />
        {error ? (
          <div className="tp-inline-alert" role="alert">
            {error}
          </div>
        ) : null}
        <div className="tp-modal-actions">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" disabled={submitting}>
            Create project
          </button>
        </div>
      </form>
    </div>
  );
}

function ReadinessIndicator({ readiness }: { readiness: ReadinessState }) {
  if (readiness.status === 'ready') {
    return <div className="tp-status tp-status-ready">Service ready</div>;
  }
  if (readiness.status === 'loading') {
    return <div className="tp-status">Checking service</div>;
  }
  return <div className="tp-status tp-status-error">Service not ready</div>;
}

function ReadinessPanel({
  readiness,
  onRetry,
}: {
  readiness: ReadinessState;
  onRetry: () => void;
}) {
  if (readiness.status === 'loading') {
    return <section className="tp-panel">Checking the local API and SQLite database...</section>;
  }

  if (readiness.status === 'ready') {
    return null;
  }

  const message =
    readiness.status === 'not-ready'
      ? firstFailedCheck(readiness.data)?.message ?? 'The service is not ready.'
      : readiness.message;

  return (
    <section className="tp-alert" role="alert">
      <h2>Service setup needs attention</h2>
      <p>{message}</p>
      <p>Start the API or initialize the SQLite database, then retry.</p>
      <button type="button" onClick={onRetry}>
        Retry readiness
      </button>
    </section>
  );
}

function firstFailedCheck(readiness: ReadinessResponse) {
  return Object.values(readiness.checks).find((check) => !check.ready);
}

function pageTitle(pathname: string) {
  if (pathname.startsWith('/baselines')) return 'Baselines';
  if (pathname.startsWith('/tasks')) return 'Tasks';
  if (pathname.startsWith('/targets')) return 'Targets';
  if (pathname.startsWith('/workflows')) return 'Workflows';
  if (pathname.startsWith('/execution-profiles')) return 'Execution Profiles';
  return 'Runs';
}

function resolveSelectedProjectId(projects: Project[], storedProjectId: string | null) {
  if (storedProjectId && projects.some((project) => project.id === storedProjectId)) {
    return storedProjectId;
  }
  return projects[0]?.id ?? null;
}

function normalizeProjectName(name: string) {
  return normalizeProjectNameForDisplay(name).toLowerCase();
}

function normalizeProjectNameForDisplay(name: string) {
  return name.trim().replace(/\s+/g, ' ');
}
