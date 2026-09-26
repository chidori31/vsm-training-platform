"""Reject inconsistent snapshots, impossible rewards and unhashed demo tokens."""

from alembic import op

revision = "20260926_06"
down_revision = "20260926_05"
branch_labels = None
depends_on = None

CHECKS = [
    (
        "scenario_sessions",
        "ck_session_envelope",
        """
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
""",
    ),
    ("session_rewards", "ck_reward_xp_cap", "xp <= 130"),
    (
        "session_rewards",
        "ck_reward_json",
        """
jsonb_typeof(competencies) = 'object'
AND NOT jsonb_path_exists(competencies, '$.* ? (@.type() != "number")')
AND NOT jsonb_path_exists(competencies,
  '$.* ? (@.type() == "number") ? (@ < 0 || @ != @.floor())')
AND jsonb_typeof(critical) = 'array'
AND NOT jsonb_path_exists(critical, '$[*] ? (@.type() != "boolean")')
""",
    ),
    ("demo_tokens", "ck_token_sha256", "token_hash ~ '^[0-9a-f]{64}$'"),
    (
        "session_start_keys",
        "ck_start_key_input",
        "scenario_version > 0 AND length(btrim(key)) > 0",
    ),
]


def upgrade() -> None:
    for table, name, condition in CHECKS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for table, name, _ in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
