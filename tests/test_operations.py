import tarfile

from scripts.backup_data import backup, restore


def test_backup_and_restore_round_trip(tmp_path):
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    source.mkdir()
    (source / "paper-state.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    backup(source, archive)
    restore(archive, restored)
    assert (restored / "paper-state.json").read_text(encoding="utf-8") == "{}"


def test_restore_rejects_unsafe_member(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 4
        import io
        handle.addfile(info, io.BytesIO(b"oops"))
    try:
        restore(archive, tmp_path / "restored")
    except ValueError:
        pass
    else:
        raise AssertionError("backup inseguro deveria ser rejeitado")
