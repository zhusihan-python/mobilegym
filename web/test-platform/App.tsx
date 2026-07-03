import { BrowserRouter } from 'react-router-dom';

import { PlatformRoutes } from './app/router';

export default function App() {
  return (
    <BrowserRouter basename="/test-platform">
      <PlatformRoutes />
    </BrowserRouter>
  );
}
