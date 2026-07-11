"""Unit tests for ModelCache (Step 03)."""
import json

from models.cache import ModelCache


class TestModelDir:
    def test_name_mangling_replaces_slash(self, tmp_path):
        cache = ModelCache(tmp_path)
        expected = tmp_path / "microsoft--harrier-oss-v1-270m"
        assert cache.model_dir("microsoft/harrier-oss-v1-270m") == expected

    def test_name_without_slash_unchanged(self, tmp_path):
        cache = ModelCache(tmp_path)
        assert cache.model_dir("standalone-model") == tmp_path / "standalone-model"

    def test_root_property(self, tmp_path):
        cache = ModelCache(tmp_path)
        assert cache.root == tmp_path


class TestIsCached:
    def test_false_when_dir_missing(self, tmp_path):
        cache = ModelCache(tmp_path)
        assert cache.is_cached("some/model") is False

    def test_false_when_dir_empty(self, tmp_path):
        cache = ModelCache(tmp_path)
        cache.model_dir("some/model").mkdir(parents=True)
        assert cache.is_cached("some/model") is False

    def test_true_after_write_meta(self, tmp_path):
        cache = ModelCache(tmp_path)
        cache.write_meta("some/model", {"a": 1})
        assert cache.is_cached("some/model") is True


class TestMeta:
    def test_read_meta_roundtrip(self, tmp_path):
        cache = ModelCache(tmp_path)
        meta = {"provider": "local", "model_name": "some/model", "note": "café"}
        cache.write_meta("some/model", meta)
        assert cache.read_meta("some/model") == meta

    def test_read_meta_none_when_absent(self, tmp_path):
        cache = ModelCache(tmp_path)
        assert cache.read_meta("nope/model") is None

    def test_write_meta_creates_parent_dirs(self, tmp_path):
        cache = ModelCache(tmp_path)
        cache.write_meta("some/model", {"x": 1})
        assert cache.model_dir("some/model").is_dir()

    def test_meta_file_is_utf8(self, tmp_path):
        cache = ModelCache(tmp_path)
        cache.write_meta("some/model", {"note": "café"})
        path = cache.model_dir("some/model") / "meta.json"
        content = path.read_text(encoding="utf-8")
        assert json.loads(content)["note"] == "café"
