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
  text: string;
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
