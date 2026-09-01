import { StrictMode, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { tokens } from './api';
import ErrorBoundary from './components/ErrorBoundary';
import { ToastProvider } from './components/Toast';
import { installGlobalErrorHandlers } from './logger';
import Dashboard from './pages/Dashboard';
import Exam from './pages/Exam';
import InviteLanding from './pages/InviteLanding';
import Login from './pages/Login';
import MagicLinkCallback from './pages/MagicLinkCallback';
import PinLogin from './pages/PinLogin';
import Submitted from './pages/Submitted';
import Analytics from './pages/admin/Analytics';
import AdminHome from './pages/admin/AdminHome';
import AttemptDetail from './pages/admin/AttemptDetail';
import CodingEvaluation from './pages/admin/CodingEvaluation';
import ExamBuilder from './pages/admin/ExamBuilder';
import LiveMonitor from './pages/admin/LiveMonitor';
import StudentDetails from './pages/admin/StudentDetails';
import './styles.css';

function Guard({ role, children }: { role: 'student' | 'admin'; children: ReactNode }) {
  if (!tokens.access || tokens.role !== role) {
    return <Navigate to={role === 'admin' ? '/admin/login' : '/'} replace />;
  }
  return <>{children}</>;
}

// Installed before the first render so a crash during initial mount is still
// reported rather than lost.
installGlobalErrorHandlers();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
    <ToastProvider>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Login />} />
        <Route path="/admin/login" element={<Login admin />} />
        <Route path="/auth/magic" element={<MagicLinkCallback />} />
        <Route path="/invite" element={<InviteLanding />} />
        <Route path="/pin-login" element={<PinLogin />} />

        <Route
          path="/dashboard"
          element={
            <Guard role="student">
              <Dashboard />
            </Guard>
          }
        />
        <Route
          path="/exam"
          element={
            <Guard role="student">
              <Exam />
            </Guard>
          }
        />
        <Route
          path="/submitted"
          element={
            <Guard role="student">
              <Submitted />
            </Guard>
          }
        />

        <Route
          path="/admin"
          element={
            <Guard role="admin">
              <AdminHome />
            </Guard>
          }
        />
        <Route
          path="/admin/exams/:examId"
          element={
            <Guard role="admin">
              <ExamBuilder />
            </Guard>
          }
        />
        <Route
          path="/admin/exams/:examId/live"
          element={
            <Guard role="admin">
              <LiveMonitor />
            </Guard>
          }
        />
        <Route
          path="/admin/exams/:examId/analytics"
          element={
            <Guard role="admin">
              <Analytics />
            </Guard>
          }
        />
        <Route
          path="/admin/coding-evaluation"
          element={
            <Guard role="admin">
              <CodingEvaluation />
            </Guard>
          }
        />
        <Route
          path="/admin/coding-evaluation/:submissionId"
          element={
            <Guard role="admin">
              <CodingEvaluation />
            </Guard>
          }
        />
        <Route
          path="/admin/student-details"
          element={
            <Guard role="admin">
              <StudentDetails />
            </Guard>
          }
        />
        <Route
          path="/admin/student-details/:attemptId"
          element={
            <Guard role="admin">
              <AttemptDetail />
            </Guard>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
    </ToastProvider>
    </ErrorBoundary>
  </StrictMode>,
);
