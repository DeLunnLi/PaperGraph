import pytest
from fastapi import HTTPException

from app.utils.common import route_errors


def test_route_errors_passes_http_exception_through():
    with pytest.raises(HTTPException) as exc_info:
        with route_errors("op"):
            raise HTTPException(status_code=404, detail="not found")
    assert exc_info.value.status_code == 404


def test_route_errors_converts_generic_exception_to_500():
    with pytest.raises(HTTPException) as exc_info:
        with route_errors("op"):
            raise RuntimeError("boom")
    assert exc_info.value.status_code == 500


def test_route_errors_no_exception():
    with route_errors("op"):
        value = 1 + 1
    assert value == 2
