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
    path: 'teacher/session/:code',
    canActivate: [teacherGuard],
    loadComponent: () =>
      import('./teacher/session.component').then((m) => m.TeacherSessionComponent),
  },
  { path: '', pathMatch: 'full', redirectTo: 'login' },
];
