from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fluxa.models import AppState, FeedState, StateError
from fluxa.state import load_state, save_state


class StatePersistenceTests(unittest.TestCase):
    def test_save_state_round_trip(self) -> None:
        state = AppState(
            bootstrap_completed=True,
            feeds={
                "demo": FeedState(
                    seen_ids=["entry-1", "entry-2"],
                    last_success_source="https://example.com/feed.xml",
                )
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            save_state(state_path, state)
            loaded_state = load_state(state_path)

        self.assertTrue(loaded_state.bootstrap_completed)
        self.assertEqual(
            loaded_state.feeds["demo"].seen_ids,
            ["entry-1", "entry-2"],
        )

    def test_save_state_removes_temp_file_when_replace_fails(self) -> None:
        state = AppState()

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"

            with patch("pathlib.Path.replace", side_effect=OSError("disk full")):
                with self.assertRaises(StateError):
                    save_state(state_path, state)

            leftover_temp_files = list(Path(temp_dir).glob(".*.tmp"))

        self.assertEqual(leftover_temp_files, [])

    def test_load_state_wraps_read_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text("{}", encoding="utf-8")

            with patch("pathlib.Path.read_text", side_effect=OSError("permission")):
                with self.assertRaises(StateError):
                    load_state(state_path)


if __name__ == "__main__":
    unittest.main()
