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
  /** `text` rendered from Markdown and sanitised server-side; bind this. */
  text_html: string;
  is_correct?: boolean; // present only in teacher views
}

/**
 * What a question asks for: one of several choices, asked twice with a
 * discussion in between, or a word for a cloud, asked once.
 */
export type QuestionKind = 'choice' | 'text';

export interface Question {
  id: number;
  position: number;
  /** Markdown source, as authored. */
  text: string;
  /** `text` rendered and sanitised server-side; bind this for display. */
  text_html: string;
  image_url: string | null;
  kind: QuestionKind;
  /** For a word cloud: how many entries each student may write. */
  max_words: number;
  /** Teacher views only: why the answer is right, and the same rendered. */
  explanation?: string;
  explanation_html?: string;
  choices: Choice[];
}

export interface Quiz {
  id: number;
  title: string;
  /** Set once the quiz is put away at the end of a course; null while in use. */
  archived_at?: string | null;
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
  /** What this student wrote, for a word-cloud question. Their own only. */
  my_text: string | null;
  /** The same, entry by entry. Absent from a server older than this field. */
  my_words?: string[];
}

export interface Word {
  text: string;
  count: number;
}

/** A halted word-cloud round: every word written, with how many wrote it. */
export interface WordCloud {
  question_id: number;
  round_id: number;
  words: Word[];
  answers: number;
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

/**
 * Two students drawn to say how they reasoned.
 *
 * `names` is the draw. `reel` is only what the projected view spins through
 * before it settles: it comes from everyone who joined the session, never from
 * the subset who answered this question, so a name flashing past discloses
 * nothing about participation.
 */
export interface Draw {
  names: string[];
  reel: string[];
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
  kind: QuestionKind;
  max_words: number;
  explanation: string;
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
  /** Canvas workflow_state: "unpublished" | "available" | "completed". */
  state: string | null;
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
  /** The course the Canvas files use; null until anything is synced. */
  current_course_id?: number | null;
  /** True when CANVAS_COURSE_ID fixes it, false when it is the last one synced. */
  course_pinned?: boolean;
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
  kind: QuestionKind;
  max_words: number;
  explanation: string;
  choices: ChoiceEditInput[];
}

/** Whether this lecture's Canvas file can be imported — see api.service. */
export interface CanvasReadiness {
  /** Null when no course is configured and nothing has been synced yet. */
  course_id: number | null;
  students: number;
  matched: number;
  unmatched: string[];
  roster_students: number;
  synced_at: string | null;
}

/** What a Markdown import found, question by question. */
export interface ImportReport {
  title: string | null;
  notes: string[];
  unused_figures: string[];
  dry_run: boolean;
  accepted: number;
  refused: number;
  imported: number;
  questions: {
    number: number | null;
    heading: string;
    ok: boolean;
    errors: string[];
    warnings: string[];
    text_html: string;
    choices: { text_html: string; is_correct: boolean }[];
    has_explanation: boolean;
  }[];
}
