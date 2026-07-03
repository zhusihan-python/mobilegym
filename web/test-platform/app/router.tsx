import { Navigate, Route, Routes } from 'react-router-dom';

import { PlatformShell } from './shell';
import { RunsPage } from '../features/runs/RunsPage';

export function PlatformRoutes() {
  return (
    <Routes>
      <Route element={<PlatformShell />}>
        <Route index element={<Navigate to="/runs" replace />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="*" element={<Navigate to="/runs" replace />} />
      </Route>
    </Routes>
  );
}
