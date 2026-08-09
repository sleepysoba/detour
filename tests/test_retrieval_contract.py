from unittest.mock import MagicMock, patch

from detour.retrieval import AttractionRepository


def test_semantic_search_is_parameterized_and_destination_scoped():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "id": 7,
            "name": "Museum of Boulder",
            "description": "A local museum.",
            "source_url": "https://example.test/museum",
            "category": "museum",
            "indoor_outdoor": "indoor",
            "tags": ["culture"],
            "similarity": 0.91,
        }
    ]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch("detour.retrieval.get_connection") as get_connection:
        get_connection.return_value.__enter__.return_value = connection
        results = AttractionRepository(database_url="postgresql://configured").semantic_search(
            destination_id=42,
            query_embedding=[0.0] * 384,
            limit=5,
        )

    sql, params = cursor.execute.call_args.args
    assert "WHERE destination_id = %s" in sql
    assert params[1] == 42
    assert params[-1] == 5
    assert results == [
        {
            "id": 7,
            "name": "Museum of Boulder",
            "description": "A local museum.",
            "source_url": "https://example.test/museum",
            "category": "museum",
            "indoor_outdoor": "indoor",
            "tags": ["culture"],
            "similarity": 0.91,
        }
    ]
