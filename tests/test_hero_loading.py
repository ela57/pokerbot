import hashlib
import json
import random

from pokerpractice.bots.base import Bot
from pokerpractice.bots.roster import _load_hero
from pokerpractice.logging_store import write_manifest


def test_selected_hero_file_is_loaded_and_snapshotted(tmp_path):
    source_root = tmp_path / "source"
    config_dir = source_root / "configs"
    hero_dir = source_root / "bots" / "versions"
    config_dir.mkdir(parents=True)
    hero_dir.mkdir(parents=True)
    hero_path = hero_dir / "my_bot_v1.py"
    hero_path.write_text(
        "from pokerpractice.bots.base import Bot\n"
        "class MyBot(Bot):\n"
        "    def act(self, obs):\n"
        "        return None\n",
        encoding="utf-8",
    )
    (config_dir / "rules.yaml").write_text("players: 8\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = write_manifest(
        run_dir, {}, {}, {}, [], [123], "versioned hero",
        source_root, hero_path,
    )
    loaded = _load_hero(hero_path, random.Random(1))
    snapshot = run_dir / manifest["hero_bot"]["snapshot"]
    assert isinstance(loaded, Bot)
    assert snapshot.read_bytes() == hero_path.read_bytes()
    assert manifest["hero_bot"]["sha256"] == hashlib.sha256(hero_path.read_bytes()).hexdigest()
    assert json.loads((run_dir / "manifest.json").read_text())["hero_bot"]["path"] == str(hero_path)
