import pytest


@pytest.fixture
def document():
    return {
        "schema_version": 1,
        "id": "synthetic-test",
        "version": 1,
        "title": "Synthetic test",
        "cycle_policy": "forbid",
        "start_node_id": "start",
        "competency_ids": ["communication"],
        "nodes": [
            {
                "id": "start",
                "text": "Make a choice",
                "time_limit_seconds": 15,
                "choices": [
                    {
                        "id": "help",
                        "text": "Help",
                        "destination": "done",
                        "explanation": "A synthetic positive consequence.",
                        "condition": {
                            "mode": "all",
                            "predicates": [
                                {
                                    "metric": "safety_rating",
                                    "operator": "gte",
                                    "value": 0,
                                }
                            ],
                        },
                        "effects": [
                            {
                                "type": "add_score",
                                "metric": "passenger_loyalty",
                                "delta": 2,
                            },
                            {
                                "type": "add_score",
                                "metric": "safety_rating",
                                "delta": 1,
                            },
                            {
                                "type": "add_score",
                                "metric": "competency",
                                "competency_id": "communication",
                                "delta": 3,
                            },
                        ],
                    },
                    {
                        "id": "decline",
                        "text": "Decline",
                        "destination": "done",
                        "explanation": "A different synthetic consequence.",
                    },
                ],
                "timeout": {
                    "destination": "expired",
                    "explanation": "No decision before the deadline.",
                    "effects": [
                        {"type": "add_score", "metric": "safety_rating", "delta": -2}
                    ],
                },
            },
            {"id": "done", "text": "Finished", "terminal": True},
            {"id": "expired", "text": "Time expired", "terminal": True},
        ],
    }
