import copy

import pytest

from app import maps
from app.maps import create_default_map, list_maps, load_map, save_map, set_tile, validate_map


@pytest.fixture(autouse=True)
def temp_maps_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(maps, "MAPS_DIR", tmp_path)
    return tmp_path


def test_create_default_map():
    map_data = create_default_map()

    assert map_data["map_id"] == "default"
    assert map_data["width"] == 20
    assert map_data["height"] == 20
    assert len(map_data["tiles"]) == 20
    assert all(len(row) == 20 for row in map_data["tiles"])
    assert all(tile == "NORMAL" for row in map_data["tiles"] for tile in row)
    assert map_data["tiles"][0] is not map_data["tiles"][1]


def test_save_and_load(temp_maps_dir):
    path = save_map(create_default_map())

    assert path == temp_maps_dir / "default.json"
    assert list_maps() == ["default"]
    assert list(temp_maps_dir.glob("*.tmp")) == []

    with pytest.raises(ValueError):
        save_map(create_default_map())


def test_saved_data_matches():
    map_data = create_default_map("arena", "Arena")
    set_tile(map_data, 5, 8, "GLASS")
    set_tile(map_data, 10, 3, "BLOCKED")
    save_map(map_data)

    loaded = load_map("arena")
    assert loaded == map_data
    assert loaded["tiles"][8][5] == "GLASS"
    assert loaded["tiles"][3][10] == "BLOCKED"


def test_change_tile_and_overwrite():
    map_data = create_default_map()
    save_map(map_data)

    set_tile(map_data, 5, 8, "GLASS")
    assert map_data["tiles"][8][5] == "GLASS"
    assert map_data["tiles"][5][8] == "NORMAL"

    save_map(map_data, overwrite=True)
    assert load_map("default")["tiles"][8][5] == "GLASS"


def test_reject_invalid_tile():
    map_data = create_default_map()

    with pytest.raises(ValueError):
        set_tile(map_data, 1, 1, "LAVA")

    map_data["tiles"][1][1] = "LAVA"
    with pytest.raises(ValueError):
        save_map(map_data)


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["tiles"].pop(),
        lambda m: m["tiles"][3].pop(),
        lambda m: m.update(width=21),
        lambda m: m.update(height=0),
        lambda m: m.update(tiles="NORMAL"),
        lambda m: m.pop("name"),
        lambda m: m.update(map_id="../evil"),
    ],
)
def test_reject_invalid_structure(change):
    map_data = copy.deepcopy(create_default_map())
    change(map_data)

    with pytest.raises(ValueError):
        validate_map(map_data)


@pytest.mark.parametrize("x, y", [(-1, 0), (0, -1), (20, 0), (0, 20)])
def test_reject_out_of_range(x, y):
    with pytest.raises(ValueError):
        set_tile(create_default_map(), x, y, "GLASS")


def test_missing_map():
    with pytest.raises(FileNotFoundError):
        load_map("nowhere")
    with pytest.raises(ValueError):
        load_map("Bad Name!")


@pytest.mark.parametrize("x, y", [(0, 0), (19, 0), (0, 19), (19, 19)])
def test_reject_blocked_spawn(x, y):
    map_data = create_default_map()
    map_data["tiles"][y][x] = "BLOCKED"

    with pytest.raises(ValueError):
        save_map(map_data)

    map_data["tiles"][y][x] = "GLASS"
    save_map(map_data)


def test_invalid_json(temp_maps_dir):
    (temp_maps_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (temp_maps_dir / "other.json").write_text(
        '{"map_id": "default", "name": "x", "width": 1, "height": 1, "tiles": [["NORMAL"]]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_map("broken")
    with pytest.raises(ValueError):
        load_map("other")
