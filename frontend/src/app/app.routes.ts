import { Routes } from '@angular/router';

import { teacherGuard } from './guards';

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./login/login.component').then((m) => m.LoginComponent),
  },
  {
    // Student entry point — this is the path the projected QR code links to.
    path: 's/:code',
    loadComponent: () =>
      import('./student/session.component').then((m) => m.StudentSessionComponent),
  },
  {
    path: 'teacher',
    canActivate: [teacherGuard],
    loadComponent: () =>
      import('./teacher/dashboard.component').then((m) => m.TeacherDashboardComponent),
  },
  {
    // Three views of a running session, each with its own URL so one can be
    // projected in a second window while the teacher drives from another.
    path: 'teacher/session/:code',
    canActivate: [teacherGuard],
    loadComponent: () =>
      import('./teacher/session-shell.component').then((m) => m.TeacherSessionShellComponent),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'join' },
      {
        path: 'join',
        loadComponent: () =>
          import('./teacher/session-join.component').then((m) => m.TeacherSessionJoinComponent),
      },
      {
        path: 'control',
        loadComponent: () =>
          import('./teacher/session-control.component').then(
            (m) => m.TeacherSessionControlComponent,
          ),
      },
      {
        path: 'report',
        loadComponent: () =>
          import('./teacher/session-report.component').then(
            (m) => m.TeacherSessionReportComponent,
          ),
      },
    ],
  },
  { path: '', pathMatch: 'full', redirectTo: 'login' },
];
