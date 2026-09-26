export const scenario = {
  id: "demo-service-situation",
  version: 1,
  title: "Работа с пассажиром",
  competency_ids: ["communication"],
};
export const login = {
  access_token: "opaque-issued-token",
  token_type: "bearer",
  expires_at: "2099-01-01T00:00:00Z",
  profile: { id: "demo-employee", display_name: "Demo" },
};
export const active = {
  session: {
    format_version: 2,
    id: "session-1",
    employee_id: "demo-employee",
    scenario_id: scenario.id,
    scenario_version: 1,
    current_node_id: "request",
    scores: [
      { metric: "passenger_loyalty", competency_id: null, value: 50 },
      { metric: "safety_rating", competency_id: null, value: 50 },
      { metric: "competency", competency_id: "communication", value: 0 },
    ],
    initial_scores: [
      { metric: "passenger_loyalty", competency_id: null, value: 50 },
      { metric: "safety_rating", competency_id: null, value: 50 },
      { metric: "competency", competency_id: "communication", value: 0 },
    ],
    scoring_policy: {
      loyalty: { minimum: 0, maximum: 100 },
      safety: { minimum: 0, maximum: 100 },
    },
    started_at: "2026-09-26T10:00:00Z",
    status: "active",
    completed_at: null,
    decisions: [],
  },
  server_time: "2026-09-26T10:00:00Z",
  deadline: "2026-09-26T10:00:40Z",
  expected_sequence: 0,
  current_node: {
    id: "request",
    text: "Что вы сделаете?",
    terminal: false,
    time_limit_seconds: 40,
  },
  available_choices: [
    { id: "explain", text: "Объяснить" },
    { id: "ignore", text: "Игнорировать" },
  ],
};
export function nextState(id = "decision-1", timeout = false) {
  return {
    ...active,
    session: {
      ...active.session,
      current_node_id: "alternative",
      decisions: [
        {
          id,
          session_id: "session-1",
          node_id: "request",
          choice_id: timeout ? "__timeout__" : "explain",
          sequence: 1,
          decided_at: "2026-09-26T10:00:01Z",
          effects: [],
          explanation: "Переход выполнен",
          score_changes: [],
        },
      ],
    },
    expected_sequence: 1,
    current_node: {
      id: "alternative",
      text: "Предложите альтернативу",
      terminal: false,
      time_limit_seconds: null,
    },
    available_choices: [{ id: "offer", text: "Предложить" }],
    deadline: null,
  };
}
export const page = { items: [scenario], total: 1, limit: 100, offset: 0 };
export const apiError = (code: string, data: unknown = null) => ({
  error: { code, message: "Server error", details: [] },
  data,
});
export function completedState(id = "decision-1") {
  const state = nextState(id);
  return {
    ...state,
    session: {
      ...state.session,
      current_node_id: "done",
      status: "completed",
      completed_at: "2026-09-26T10:00:01Z",
    },
    current_node: {
      id: "done",
      text: "Завершено",
      terminal: true,
      time_limit_seconds: null,
    },
    available_choices: [],
  };
}
export function completedResult(id = "decision-1") {
  return {
    session: completedState(id).session,
    summary: {
      session_id: "session-1",
      scenario_id: scenario.id,
      scenario_version: 1,
      status: "completed",
      completed_at: "2026-09-26T10:00:01Z",
      duration_seconds: 1,
      decision_count: 1,
      passenger_loyalty: 50,
      safety_rating: 50,
      competencies: [{ competency_id: "communication", value: 0 }],
    },
  };
}
