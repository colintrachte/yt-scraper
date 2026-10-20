import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class DiscoveryPersistenceTests(unittest.TestCase):
    def test_persistence_merges_new_discovery_with_existing_data(self):
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        ) as temp_dir:
            out_dir = Path(temp_dir)
            main.persist_discovery(
                [{"id": "oldvideo001", "title": "Old"}],
                [{"playlist_id": "PL-old", "title": "Old playlist"}],
                [{"playlist_id": "PL-old", "video_id": "oldvideo001"}],
                out_dir,
            )

            main.persist_discovery(
                [{"id": "newvideo002", "title": "New"}],
                [],
                [],
                out_dir,
            )

            videos = json.loads((out_dir / "videos_meta.json").read_text())
            playlists = self._read_jsonl(out_dir / "playlists.jsonl")
            mappings = self._read_jsonl(out_dir / "playlist_videos.jsonl")

            self.assertEqual({v["id"] for v in videos}, {"oldvideo001", "newvideo002"})
            self.assertEqual(
                (out_dir / "videos.txt").read_text().splitlines(),
                ["oldvideo001", "newvideo002"],
            )
            self.assertEqual([p["playlist_id"] for p in playlists], ["PL-old"])
            self.assertEqual(
                [(m["playlist_id"], m["video_id"]) for m in mappings],
                [("PL-old", "oldvideo001")],
            )

    def test_persistence_updates_existing_records_without_duplicates(self):
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        ) as temp_dir:
            out_dir = Path(temp_dir)
            main.persist_discovery(
                [{"id": "samevideo01", "title": "Before"}],
                [{"playlist_id": "PL-same", "title": "Before"}],
                [{"playlist_id": "PL-same", "video_id": "samevideo01", "position": 9}],
                out_dir,
            )
            main.persist_discovery(
                [{"id": "samevideo01", "title": "After"}],
                [{"playlist_id": "PL-same", "title": "After"}],
                [{"playlist_id": "PL-same", "video_id": "samevideo01", "position": 1}],
                out_dir,
            )

            self.assertEqual(
                json.loads((out_dir / "videos_meta.json").read_text())[0]["title"],
                "After",
            )
            self.assertEqual(
                self._read_jsonl(out_dir / "playlists.jsonl")[0]["title"], "After"
            )
            self.assertEqual(
                self._read_jsonl(out_dir / "playlist_videos.jsonl")[0]["position"], 1
            )

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text().splitlines() if line]


if __name__ == "__main__":
    unittest.main()
