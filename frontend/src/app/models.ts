export type Role = 'teacher' | 'student';
export type Phase = 'pre' | 'post';

export interface User {
  username: string;
  display_name: string;
  role: Role;
}

export interface Choice {
  id: number;
  position: number;
  text: string;
  is_correct?: boolean; // present only in teacher views
}

export interface Question {
  id: number;
  position: number;
  /** Markdown source, as authored. */
  text: string;
  /** `text` rendered and sanitised server-side; bind this for display. */
  text_html: string;
  image_url: string | null;
  choices: Choice[];
}

export interface Quiz {
  id: number;
  title: string;
  questions: Question[];
}

export interface Round {
  id: number;
  question_id: number;
  phase: Phase;
  opened_at: string;
  closed_at: string | null;
}

export interface SessionState {
  code: string;
  quiz_id: number;
  quiz_title: string;
  open_round: Round | null;
  question: Question | null;
  my_choice_id: number | null;
}

export interface Histogram {
  round_id: number;
  phase: Phase;
  counts: Record<number, number>;
  total: number;
}

export interface Participants {
  joined: number;
  connected: number;
}

export interface ParticipantAnswer {
  question_id: number;
  pre: boolean | null;
  post: boolean | null;
}

export interface ParticipantRow {
  username: string;
  display_name: string;
  answers: ParticipantAnswer[];
  answered: number;
  pre_correct: number;
  post_correct: number;
}

export interface ParticipationReport {
  questions: Question[];
  rows: ParticipantRow[];
}

export interface LiveCount {
  open_round: Round | null;
  answered: number;
}

export interface Comparison {
  question_id: number;
  pre: Record<number, number> | null;
  post: Record<number, number> | null;
}

export interface QuizSession {
  id: number;
  code: string;
  quiz_id: number;
  created_at: string;
}

// Payload shapes for creating a question (no server-assigned ids yet).
export interface ChoiceInput {
  text: string;
  is_correct: boolean;
}

export interface QuestionInput {
  text: string;
  image_url: string | null;
  choices: ChoiceInput[];
}

/**
 * A choice in an edit. `id` marks one that already exists, so the server can
 * tell a reworded choice from a new one — answers point at choice ids, and a
 * choice students have answered must not be dropped.
 */
export interface ChoiceEditInput extends ChoiceInput {
  id?: number;
}

/** Which login forms a deployment offers. */
export interface LoginMethods {
  mock_login: boolean;
  roster_login: boolean;
  oidc: boolean;
}

/** A Canvas course the teacher can sync a roster from. */
export interface CanvasCourse {
  id: number;
  name: string;
  code: string | null;
}

export interface SyncedCourse {
  course_id: number;
  students: number;
  synced_at: string;
}

export interface RosterStatus {
  canvas_configured: boolean;
  canvas_base_url: string;
  courses: SyncedCourse[];
}

export interface SyncSummary {
  course_id: number;
  total: number;
  added: number;
  updated: number;
  removed: number;
}

export interface QuestionEditInput {
  text: string;
  image_url: string | null;
  choices: ChoiceEditInput[];
}
