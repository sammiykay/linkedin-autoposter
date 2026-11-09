import os, tempfile, importlib
from datetime import datetime, timedelta

from app import db, linkedin, images

def test_escape_keeps_hashtags_and_escapes_specials():
    out = linkedin.escape_commentary("Hello (world) #saas @bob_1 *x*")
    assert "#saas" in out
    assert "\\(world\\)" in out
    assert "\\@bob" in out and "\\_1" in out

def test_claim_due_only_claims_due_and_is_atomic():
    past = (datetime.now() - timedelta(minutes=5)).strftime(db.FMT)
    future = (datetime.now() + timedelta(hours=2)).strftime(db.FMT)
    a = db.add_post("due", past, "scheduled")
    b = db.add_post("later", future, "scheduled")
    first = db.claim_due(24)
    second = db.claim_due(24)
    assert [p["id"] for p in first] == [a]
    assert second == []
    assert db.get(b)["status"] == "scheduled"
    assert db.get(a)["status"] == "posting"

def test_overdue_marked_missed():
    old = (datetime.now() - timedelta(hours=30)).strftime(db.FMT)
    pid = db.add_post("old", old, "scheduled")
    assert db.claim_due(24) == []
    assert db.get(pid)["status"] == "missed"

def test_recover_stuck():
    pid = db.add_post("x", db.now_str(), "posting")
    db.recover_stuck()
    assert db.get(pid)["status"] == "uncertain"

def test_image_rejects_bad_ext_and_fake_image():
    import pytest
    with pytest.raises(images.ImageError):
        images.save_upload("a.exe", b"123")
    with pytest.raises(images.ImageError):
        images.save_upload("a.png", b"not really an image")

def test_image_path_traversal_blocked():
    import pytest
    with pytest.raises(images.ImageError):
        images.resolve("../../etc/passwd")
