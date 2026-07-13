import { Navigate, Route, Routes } from 'react-router-dom';

import { PlatformShell } from './shell';
import { RunsPage } from '../features/runs/RunsPage';
import { RunDetailPage } from '../features/runs/RunDetailPage';
import { TasksPage } from '../features/tasks/TasksPage';
import { TargetsPage } from '../features/targets/TargetsPage';
import { WorkflowsPage } from '../features/workflows/WorkflowsPage';
import { BaselineDetailPage, BaselinesPage } from '../features/baselines/BaselinesPage';

export function PlatformRoutes() {
  return (
    <Routes>
      <Route element={<PlatformShell />}>
        <Route index element={<Navigate to="/runs" replace />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="baselines" element={<BaselinesPage />} />
        <Route path="baselines/:baselineId" element={<BaselineDetailPage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="targets" element={<TargetsPage />} />
        <Route path="workflows" element={<WorkflowsPage />} />
        <Route path="*" element={<Navigate to="/runs" replace />} />
      </Route>
    </Routes>
  );
}
