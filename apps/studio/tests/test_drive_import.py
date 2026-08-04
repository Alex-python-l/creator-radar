import pytest
from pathlib import Path
from app.services import drive_import


def test_parse_folder_id_from_standard_url():
    url = "https://drive.google.com/drive/folders/1AbC_def-GHI123456789"
    assert drive_import.parse_folder_id(url) == "1AbC_def-GHI123456789"


def test_parse_folder_id_with_query_string():
    url = "https://drive.google.com/drive/folders/1AbC_def-GHI123456789?usp=sharing"
    assert drive_import.parse_folder_id(url) == "1AbC_def-GHI123456789"


def test_parse_folder_id_with_user_segment():
    url = "https://drive.google.com/drive/u/0/folders/1AbC_def-GHI123456789"
    assert drive_import.parse_folder_id(url) == "1AbC_def-GHI123456789"


def test_parse_folder_id_accepts_bare_id():
    assert drive_import.parse_folder_id("1AbC_def-GHI123456789xyz") \
        == "1AbC_def-GHI123456789xyz"


def test_parse_folder_id_rejects_non_folder_url():
    with pytest.raises(ValueError):
        drive_import.parse_folder_id("https://drive.google.com/file/d/abc/view")


def test_parse_folder_id_rejects_empty():
    with pytest.raises(ValueError):
        drive_import.parse_folder_id("")


def test_is_video():
    assert drive_import.is_video("clip.MP4") is True
    assert drive_import.is_video("a.mov") is True
    assert drive_import.is_video("notes.txt") is False
    assert drive_import.is_video("noext") is False


def test_unique_path_uses_original_name_when_free(tmp_path):
    assert drive_import.unique_path(tmp_path, "v.mp4", "FID1") == tmp_path / "v.mp4"


def test_unique_path_disambiguates_on_collision(tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"x")
    assert drive_import.unique_path(tmp_path, "v.mp4", "FID1") == tmp_path / "v_FID1.mp4"


def test_unique_path_raises_when_disambiguated_path_also_exists(tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"x")
    (tmp_path / "v_FID1.mp4").write_bytes(b"x")
    with pytest.raises(FileExistsError):
        drive_import.unique_path(tmp_path, "v.mp4", "FID1")


def test_get_state_returns_snapshot_with_expected_keys():
    s = drive_import.get_state()
    assert set(s) >= {"state", "total", "done", "skipped", "failed",
                      "current", "errors", "started_at", "finished_at"}
    s["errors"].append("mutating the copy must not affect module state")
    assert drive_import.get_state()["errors"] == []


from collections import namedtuple

FakeFile = namedtuple("FakeFile", ["id", "path", "local_path"])


def test_run_import_downloads_selected_files(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)
    drive_import.run_import([{"id": "ID1", "name": "a.mp4"},
                             {"id": "ID2", "name": "b.mov"}])
    vids = vid_repo.list_all(db)
    assert {v["drive_file_id"] for v in vids} == {"ID1", "ID2"}
    # Drive import matches local upload: every imported video gets all
    # supported platform publications in PENDING.
    for v in vids:
        assert sorted(p["platform"] for p in v["publications"]) == [
            "facebook", "instagram", "tiktok", "youtube"]
        assert {p["result"] for p in v["publications"]} == {"PENDING"}
    st = drive_import.get_state()
    assert st["state"] == "done" and st["done"] == 2 and st["failed"] == 0


def test_run_import_skips_already_imported(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    vid_repo.create(db, "old.mp4", str(tmp_path / "old.mp4"), drive_file_id="ID1")

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)
    drive_import.run_import([{"id": "ID1", "name": "old.mp4"},
                             {"id": "ID2", "name": "new.mp4"}])
    st = drive_import.get_state()
    assert st["done"] == 1 and st["skipped"] == 1


def test_run_import_isolates_per_file_failure(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")

    def fake_download(id=None, output=None, **kw):
        if id == "BAD":
            raise RuntimeError("network error")
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)
    drive_import.run_import([{"id": "OK", "name": "good.mp4"},
                             {"id": "BAD", "name": "bad.mp4"}])
    vids = vid_repo.list_all(db)
    assert len(vids) == 1 and vids[0]["drive_file_id"] == "OK"
    st = drive_import.get_state()
    assert st["done"] == 1 and st["failed"] == 1 and st["state"] == "done"
    assert any("bad.mp4" in e for e in st["errors"])


def test_run_import_writes_job_run(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import job_runs as jobs_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)
    drive_import.run_import([{"id": "Z", "name": "z.mp4"}])
    jr = jobs_repo.recent(db, 1)[0]
    assert jr["job_type"] == "drive_import" and jr["status"] == "ok"
    assert "1 imported" in jr["summary"]


def test_start_import_without_proxy_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    with pytest.raises(drive_import.ProxyNotConfigured):
        drive_import.start_import([{"id": "X", "name": "a.mp4"}])


def test_start_import_empty_selection_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    with pytest.raises(ValueError):
        drive_import.start_import([])


def test_start_import_rejects_concurrent(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    monkeypatch.setitem(drive_import._import_state, "state", "running")
    with pytest.raises(drive_import.ImportAlreadyRunning):
        drive_import.start_import([{"id": "X", "name": "a.mp4"}])


def test_start_import_launches_background_thread(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    monkeypatch.setitem(drive_import._import_state, "state", "idle")
    called = {}
    monkeypatch.setattr(
        drive_import, "run_import",
        lambda files, group_id=None, platforms=None: called.update(files=files))
    t = drive_import.start_import([{"id": "X", "name": "a.mp4"}])
    t.join(timeout=2)
    assert called.get("files") == [{"id": "X", "name": "a.mp4"}]


def test_list_folder_returns_videos_with_imported_flag(db, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    vid_repo.create(db, "old.mp4", "/x/old.mp4", drive_file_id="ID1")
    listing = [FakeFile("ID1", "f/old.mp4", "x"),
               FakeFile("ID2", "f/new.mp4", "x"),
               FakeFile("ID3", "f/notes.txt", "x")]
    monkeypatch.setattr(drive_import.gdown, "download_folder", lambda **kw: listing)
    files = drive_import.list_folder("https://drive.google.com/drive/folders/TESTFOLDER01")
    assert files == [
        {"id": "ID1", "name": "old.mp4", "already_imported": True},
        {"id": "ID2", "name": "new.mp4", "already_imported": False},
    ]


def test_list_folder_without_proxy_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    with pytest.raises(drive_import.ProxyNotConfigured):
        drive_import.list_folder("https://drive.google.com/drive/folders/ABCDEFGHIJ")


def test_list_folder_bad_url_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    with pytest.raises(ValueError):
        drive_import.list_folder("nonsense")
