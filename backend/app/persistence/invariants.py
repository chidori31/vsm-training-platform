"""Database envelope checks complement, rather than replace, domain replay."""

SESSION_ENVELOPE = """
CASE WHEN jsonb_typeof(snapshot) = 'object'
  AND jsonb_typeof(snapshot->'decisions') = 'array'
THEN (
  snapshot->'format_version' = '2'::jsonb
  AND snapshot->'id' = to_jsonb(id)
  AND snapshot->'scenario_id' = to_jsonb(scenario_id)
  AND snapshot->'scenario_version' = to_jsonb(scenario_version)
  AND snapshot->'status' = to_jsonb(state)
  AND jsonb_typeof(snapshot->'employee_id') = 'string'
  AND length(btrim(snapshot->>'employee_id')) > 0
  AND jsonb_typeof(snapshot->'current_node_id') = 'string'
  AND length(btrim(snapshot->>'current_node_id')) > 0
  AND jsonb_typeof(snapshot->'scores') = 'array'
  AND jsonb_typeof(snapshot->'initial_scores') = 'array'
  AND jsonb_typeof(snapshot->'scoring_policy') = 'object'
  AND jsonb_typeof(snapshot->'started_at') = 'string'
  AND snapshot ? 'completed_at'
  AND ((state = 'completed' AND jsonb_typeof(snapshot->'completed_at') = 'string')
       OR (state = 'active' AND snapshot->'completed_at' = 'null'::jsonb))
  AND jsonb_array_length(snapshot->'decisions') = revision
) IS TRUE ELSE FALSE END
"""

REWARD_JSON = """
jsonb_typeof(competencies) = 'object'
AND NOT jsonb_path_exists(competencies, '$.* ? (@.type() != "number")')
AND NOT jsonb_path_exists(competencies,
  '$.* ? (@.type() == "number") ? (@ < 0 || @ != @.floor())')
AND jsonb_typeof(critical) = 'array'
AND NOT jsonb_path_exists(critical, '$[*] ? (@.type() != "boolean")')
"""
