"""Tests for the semantic search endpoint (api/routers/search.py)."""

from contextlib import asynccontextmanager
from unittest import mock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import create_app


def _async_cm(conn):
    """Async context-manager factory; passes the mock conn through."""
    @asynccontextmanager
    async def _ctx():
        yield conn
    return _ctx


def _async_return(value):
    """Wrap a value in a coroutine for return-value mocking of async helpers."""
    async def _f(*args, **kwargs):
        return value
    return _f


def _make_async_conn_select_rows(rows):
    """A minimal async conn that always returns `rows` from any execute()."""
    class _Cursor:
        async def fetchall(self):
            return rows

        async def fetchone(self):
            return rows[0] if rows else None

        async def close(self):
            pass

    class _Conn:
        async def execute(self, *a, **kw):
            return _Cursor()

    return _Conn()


@pytest.fixture()
def client():
    app = create_app()
    return TestClient(app)


class TestSearch:
    """Tests for GET /api/search."""

    def test_search_disabled(self, client):
        """When show_semantic_search is False, returns error message."""
        disabled_config = {
            "features": {"show_semantic_search": False},
            "display": {"tags_per_photo": 3},
        }
        with mock.patch.dict("api.routers.search.VIEWER_CONFIG", disabled_config):
            resp = client.get("/api/search", params={"q": "sunset"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["photos"] == []
        assert body["total"] == 0
        assert "error" in body
        assert "disabled" in body["error"].lower()

    def test_no_embeddings(self, client):
        """When _load_embedding_matrix returns (None, []), returns empty photos."""
        async_conn = _make_async_conn_select_rows([])

        with (
            mock.patch("api.routers.search.VIEWER_CONFIG", {
                "features": {"show_semantic_search": True},
                "display": {"tags_per_photo": 3},
            }),
            mock.patch("api.routers.search.get_async_db", _async_cm(async_conn)),
            mock.patch("api.routers.search.get_visibility_clause", return_value=("1=1", [])),
            mock.patch("api.db_helpers.get_existing_columns", return_value={"path", "aggregate"}),
            mock.patch("api.routers.search.get_photos_from_clause", return_value=("photos", [])),
            mock.patch("api.db_helpers.get_preference_columns", return_value={}),
            mock.patch("api.routers.search._load_embedding_matrix", _async_return((None, []))),
            mock.patch("api.routers.search._has_fts", _async_return(False)),
            mock.patch("api.routers.search._check_vec_available", _async_return(False)),
            mock.patch("api.routers.search._encode_text", return_value=np.array([1.0, 0.0], dtype=np.float32)),
        ):
            resp = client.get("/api/search", params={"q": "mountains"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["photos"] == []
        assert body["total"] == 0


    def test_successful_search(self, client):
        """Mock matrix with 3 embeddings, text_emb that matches 2 above threshold."""
        matrix = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0, 0.0],
        ], dtype=np.float32)
        paths = ["/photos/a.jpg", "/photos/b.jpg", "/photos/c.jpg"]
        text_emb = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

        # The fetch of full photo rows for matching paths happens last.
        async_conn = _make_async_conn_select_rows([
            {"path": "/photos/a.jpg", "filename": "a.jpg", "tags": "sunset,sky",
             "date_taken": "2024:06:15 18:30:00", "aggregate": 8.5},
            {"path": "/photos/c.jpg", "filename": "c.jpg", "tags": "dawn",
             "date_taken": "2024:07:01 06:00:00", "aggregate": 7.2},
        ])

        async def _no_op_attach(*args, **kwargs):
            return None

        with (
            mock.patch("api.routers.search.VIEWER_CONFIG", {
                "features": {"show_semantic_search": True},
                "display": {"tags_per_photo": 3},
            }),
            mock.patch("api.routers.search.get_async_db", _async_cm(async_conn)),
            mock.patch("api.routers.search.get_visibility_clause", return_value=("1=1", [])),
            mock.patch("api.db_helpers.get_existing_columns", return_value={"path", "aggregate"}),
            mock.patch("api.routers.search.get_photos_from_clause", return_value=("photos", [])),
            mock.patch("api.db_helpers.get_preference_columns", return_value={}),
            mock.patch("api.routers.search._load_embedding_matrix", _async_return((matrix, paths))),
            mock.patch("api.routers.search._encode_text", return_value=text_emb),
            mock.patch("api.routers.search._has_fts", _async_return(False)),
            mock.patch("api.routers.search._check_vec_available", _async_return(False)),
            mock.patch("api.routers.search.attach_person_data_async", _no_op_attach),
            mock.patch("api.routers.search.sanitize_float_values"),
        ):
            resp = client.get("/api/search", params={"q": "sunset", "threshold": 0.15})

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["photos"]) == 2
        for photo in body["photos"]:
            assert "similarity" in photo
            assert photo["similarity"] > 0
        assert body["photos"][0]["path"] == "/photos/a.jpg"
        assert body["photos"][1]["path"] == "/photos/c.jpg"


    def test_dimension_mismatch(self, client):
        """When text_emb dimension != matrix columns, returns empty."""
        matrix = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.5, 0.5, 0.0, 0.0],
        ], dtype=np.float32)
        paths = ["/photos/a.jpg", "/photos/b.jpg", "/photos/c.jpg"]
        text_emb = np.array([1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)  # wrong dim
        async_conn = _make_async_conn_select_rows([])

        with (
            mock.patch("api.routers.search.VIEWER_CONFIG", {
                "features": {"show_semantic_search": True},
                "display": {"tags_per_photo": 3},
            }),
            mock.patch("api.routers.search.get_async_db", _async_cm(async_conn)),
            mock.patch("api.routers.search.get_visibility_clause", return_value=("1=1", [])),
            mock.patch("api.db_helpers.get_existing_columns", return_value={"path", "aggregate"}),
            mock.patch("api.routers.search.get_photos_from_clause", return_value=("photos", [])),
            mock.patch("api.db_helpers.get_preference_columns", return_value={}),
            mock.patch("api.routers.search._load_embedding_matrix", _async_return((matrix, paths))),
            mock.patch("api.routers.search._encode_text", return_value=text_emb),
            mock.patch("api.routers.search._has_fts", _async_return(False)),
            mock.patch("api.routers.search._check_vec_available", _async_return(False)),
        ):
            resp = client.get("/api/search", params={"q": "sunset"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["photos"] == []
        assert body["total"] == 0


    def test_no_results_above_threshold(self, client):
        """When all similarities are below threshold, returns empty."""
        matrix = np.array([
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ], dtype=np.float32)
        paths = ["/photos/a.jpg", "/photos/b.jpg"]
        text_emb = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        async_conn = _make_async_conn_select_rows([])

        with (
            mock.patch("api.routers.search.VIEWER_CONFIG", {
                "features": {"show_semantic_search": True},
                "display": {"tags_per_photo": 3},
            }),
            mock.patch("api.routers.search.get_async_db", _async_cm(async_conn)),
            mock.patch("api.routers.search.get_visibility_clause", return_value=("1=1", [])),
            mock.patch("api.db_helpers.get_existing_columns", return_value={"path", "aggregate"}),
            mock.patch("api.routers.search.get_photos_from_clause", return_value=("photos", [])),
            mock.patch("api.db_helpers.get_preference_columns", return_value={}),
            mock.patch("api.routers.search._load_embedding_matrix", _async_return((matrix, paths))),
            mock.patch("api.routers.search._encode_text", return_value=text_emb),
            mock.patch("api.routers.search._has_fts", _async_return(False)),
            mock.patch("api.routers.search._check_vec_available", _async_return(False)),
        ):
            resp = client.get("/api/search", params={"q": "sunset", "threshold": 0.15})

        assert resp.status_code == 200
        body = resp.json()
        assert body["photos"] == []
        assert body["total"] == 0


    def test_search_error_returns_safe(self, client):
        """When _encode_text raises, returns error dict not 500."""
        matrix = np.array([[1.0, 0.0]], dtype=np.float32)
        paths = ["/photos/a.jpg"]
        async_conn = _make_async_conn_select_rows([])

        with (
            mock.patch("api.routers.search.VIEWER_CONFIG", {
                "features": {"show_semantic_search": True},
                "display": {"tags_per_photo": 3},
            }),
            mock.patch("api.routers.search.get_async_db", _async_cm(async_conn)),
            mock.patch("api.routers.search.get_visibility_clause", return_value=("1=1", [])),
            mock.patch("api.db_helpers.get_existing_columns", return_value={"path", "aggregate"}),
            mock.patch("api.routers.search.get_photos_from_clause", return_value=("photos", [])),
            mock.patch("api.db_helpers.get_preference_columns", return_value={}),
            mock.patch("api.routers.search._load_embedding_matrix", _async_return((matrix, paths))),
            mock.patch("api.routers.search._encode_text", side_effect=RuntimeError("GPU OOM")),
            mock.patch("api.routers.search._has_fts", _async_return(False)),
            mock.patch("api.routers.search._check_vec_available", _async_return(False)),
        ):
            resp = client.get("/api/search", params={"q": "sunset"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["photos"] == []
        assert "error" in body


    def test_query_validation(self, client):
        """Empty query returns 422 validation error."""
        resp = client.get("/api/search", params={"q": ""})
        assert resp.status_code == 422
