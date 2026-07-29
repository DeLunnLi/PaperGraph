from app.core.search.normalize import strip_arxiv_version
from app.utils.common import normalize_arxiv_id


def test_strip_arxiv_version_removes_suffix():
    assert strip_arxiv_version("2401.12345v3") == "2401.12345"


def test_strip_arxiv_version_no_suffix_unchanged():
    assert strip_arxiv_version("2401.12345") == "2401.12345"


def test_strip_arxiv_version_none_and_empty():
    assert strip_arxiv_version(None) == ""
    assert strip_arxiv_version("") == ""


def test_strip_arxiv_version_does_not_touch_inner_v():
    # "vae" contains v but it is not a trailing version suffix
    assert strip_arxiv_version("vae-model") == "vae-model"


def test_normalize_arxiv_id_strips_and_lowercases():
    assert normalize_arxiv_id("2401.12345V3") == "2401.12345"
    assert normalize_arxiv_id(None) is None
    assert normalize_arxiv_id("  2401.12345  ") == "2401.12345"
