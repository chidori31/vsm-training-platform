export interface ScenarioSummary {
  id: string;
  version: number;
  title: string;
  competency_ids: string[];
}
export interface MetricRef {
  metric: "passenger_loyalty" | "safety_rating" | "competency";
  competency_id: string | null;
}
export interface Score extends MetricRef {
  value: number;
}
export interface ScoreChange extends MetricRef {
  before: number;
  requested_delta: number;
  after: number;
  applied_delta: number;
  explanation: string;
}
export interface ScoreEffect extends MetricRef {
  type: "add_score";
  delta: number;
}
export interface Decision {
  id: string;
  session_id: string;
  node_id: string;
  choice_id: string;
  sequence: number;
  decided_at: string;
  effects: ScoreEffect[];
  explanation: string;
  score_changes: ScoreChange[];
}
export interface ScoreBounds {
  minimum: number;
  maximum: number;
}
export interface SessionSnapshot {
  format_version: 2;
  id: string;
  employee_id: string;
  scenario_id: string;
  scenario_version: number;
  current_node_id: string;
  scores: Score[];
  initial_scores: Score[];
  scoring_policy: { loyalty: ScoreBounds; safety: ScoreBounds };
  started_at: string;
  status: "active" | "completed";
  completed_at: string | null;
  decisions: Decision[];
}
export interface SessionState {
  session: SessionSnapshot;
  server_time: string;
  deadline: string | null;
  expected_sequence: number;
  current_node: {
    id: string;
    text: string;
    terminal: boolean;
    time_limit_seconds: number | null;
  };
  available_choices: { id: string; text: string }[];
}
export interface SessionResult {
  summary: {
    session_id: string;
    scenario_id: string;
    scenario_version: number;
    status: "completed";
    decision_count: number;
    duration_seconds: number;
    passenger_loyalty: number;
    safety_rating: number;
    completed_at: string;
    competencies: { competency_id: string; value: number }[];
  };
  session: SessionSnapshot;
}
export interface DecisionCommand {
  decision_id: string;
  node_id: string;
  choice_id: string;
  expected_sequence: number;
}
export interface StartCommand {
  key: string;
  scenario_id: string;
  scenario_version: number;
}
export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  profile: { id: string; display_name: string };
}
export interface CatalogPage {
  items: ScenarioSummary[];
  total: number;
  limit: number;
  offset: number;
}
export interface RunnerView {
  identity: { id: string; display_name: string } | null;
  phase: "loading" | "ready" | "starting" | "active" | "completed";
  catalog: ScenarioSummary[];
  state: SessionState | null;
  result: SessionResult | null;
  error: string | null;
  connection: "online" | "offline" | "reconnecting";
  busy: boolean;
  remainingSeconds: number | null;
  pendingChoiceId: string | null;
}
