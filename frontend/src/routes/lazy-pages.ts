import { lazy } from 'react'

// Route-level code splitting. Kept in its own module so the router file stays
// a pure route table.

export const DashboardPage = lazy(() => import('@/pages/dashboard-page'))
export const ProjectsPage = lazy(() => import('@/pages/projects-page'))
export const TasksPage = lazy(() => import('@/pages/tasks-page'))
export const PlannerPage = lazy(() => import('@/pages/planner-page'))
export const KnowledgePage = lazy(() => import('@/pages/knowledge-page'))
export const SearchPage = lazy(() => import('@/pages/search-page'))
export const AnalyticsPage = lazy(() => import('@/pages/analytics-page'))
export const DeveloperPage = lazy(() => import('@/pages/developer-page'))
export const LearningPage = lazy(() => import('@/pages/learning-page'))
export const CareerPage = lazy(() => import('@/pages/career-page'))
export const AssistantPage = lazy(() => import('@/pages/assistant-page'))
export const ExperimentsPage = lazy(() => import('@/pages/experiments-page'))
export const SettingsPage = lazy(() => import('@/pages/settings-page'))
export const LoginPage = lazy(() => import('@/pages/login-page'))
export const RegisterPage = lazy(() => import('@/pages/register-page'))
export const NotFoundPage = lazy(() => import('@/pages/not-found-page'))