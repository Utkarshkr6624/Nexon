import { Navigate, Outlet, createBrowserRouter } from 'react-router-dom'

import { RouteErrorBoundary } from '@/components/feedback/app-error-boundary'
import { AppLayout } from '@/routes/app-layout'
import { AuthRouteBoundary } from '@/routes/auth-boundary'
import { RequireAnonymous } from '@/routes/guards'
import {
  AnalyticsPage,
  AssistantPage,
  CareerPage,
  DashboardPage,
  DeveloperPage,
  ExperimentsPage,
  KnowledgePage,
  LearningPage,
  LoginPage,
  NotFoundPage,
  PlannerPage,
  ProjectsPage,
  RegisterPage,
  SearchPage,
  SettingsPage,
  TasksPage,
} from '@/routes/lazy-pages'

// Paths mirror `@/features/modules/catalog`, which owns the navigation.
export const router = createBrowserRouter([
  // The product opens on the dashboard, not on an empty index.
  { path: '/', element: <Navigate to="/dashboard" replace /> },

  {
    element: <AppLayout />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: '/dashboard', element: <DashboardPage /> },
      { path: '/projects', element: <ProjectsPage /> },
      { path: '/tasks', element: <TasksPage /> },
      { path: '/planner', element: <PlannerPage /> },
      { path: '/knowledge', element: <KnowledgePage /> },
      { path: '/search', element: <SearchPage /> },
      { path: '/analytics', element: <AnalyticsPage /> },
      { path: '/developer', element: <DeveloperPage /> },
      { path: '/learning', element: <LearningPage /> },
      { path: '/career', element: <CareerPage /> },
      { path: '/assistant', element: <AssistantPage /> },
      { path: '/experiments', element: <ExperimentsPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },

  {
    element: (
      <RequireAnonymous>
        <AuthRouteBoundary>
          <Outlet />
        </AuthRouteBoundary>
      </RequireAnonymous>
    ),
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: '/login', element: <LoginPage /> },
      { path: '/register', element: <RegisterPage /> },
    ],
  },
])