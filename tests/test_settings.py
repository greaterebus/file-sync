import json

from filesync.settings import Settings, load_settings, save_settings, settings_from_json, settings_to_json


def test_settings_round_trip_through_json():
    settings = Settings(source="/Users/me/Scans/Staging", destination="/Users/me/SynologyDrive/Scans")
    assert settings_from_json(settings_to_json(settings)) == settings
    assert json.loads(settings_to_json(settings)) == {
        "source": "/Users/me/Scans/Staging",
        "destination": "/Users/me/SynologyDrive/Scans",
    }


def test_unknown_and_missing_keys_are_tolerated():
    assert settings_from_json('{"source": "/a", "extra": 1}') == Settings(source="/a", destination="")


def test_save_then_load(tmp_path):
    path = tmp_path / "FileSync" / "settings.json"
    settings = Settings(source="/a", destination="/b")
    save_settings(settings, path)
    assert load_settings(path) == settings
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]  # no temp files left behind


def test_missing_or_corrupt_settings_load_as_empty(tmp_path):
    assert load_settings(tmp_path / "missing.json") == Settings()
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert load_settings(corrupt) == Settings()
    corrupt.write_text("[1, 2]")
    assert load_settings(corrupt) == Settings()


def test_is_complete():
    assert not Settings().is_complete
    assert not Settings(source="/a").is_complete
    assert Settings(source="/a", destination="/b").is_complete
