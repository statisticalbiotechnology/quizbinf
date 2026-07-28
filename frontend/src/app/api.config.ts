// In development the Angular dev server runs on :4200 and the API on :8000.
// In production both are served from the same origin, so the base is empty.
export const API_BASE = location.port === '4200' ? 'http://localhost:8000' : '';
