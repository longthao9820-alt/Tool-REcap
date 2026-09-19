from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from recap_tool.ui import RecapStudioApp


class VoiceSelectionTests(TestCase):
    def test_render_blocks_retired_voice_before_queue_starts(self) -> None:
        app = SimpleNamespace(
            projects=[SimpleNamespace(enabled=True, source_video=__file__, voice_id="kokoro.af_heart", episode_id="S01E01", recap_language="en-US")],
            gpu_var=Mock(get=Mock(return_value=False)),
            voice_manager=Mock(list_voices=Mock(return_value=[])),
            queue=Mock(),
            _settings_changed=Mock(),
        )
        with patch("recap_tool.ui.messagebox.showerror") as error:
            RecapStudioApp._run_enabled(app)
        error.assert_called_once()
        self.assertIn("S01E01", error.call_args.args[1])
        app.queue.run.assert_not_called()
        app._settings_changed.assert_not_called()

    def test_release_runtime_falls_back_only_for_release_layout(self) -> None:
        from recap_tool.voice_system import application_root

        executable = Path(__file__).resolve().parent / "RecapStudio.exe"
        with patch("sys.frozen", True, create=True), patch("sys.executable", str(executable)):
            self.assertEqual(application_root(), executable.parent)

    def test_mode_combobox_switches_to_full_episode_outputs(self) -> None:
        record = SimpleNamespace(
            id="episode-1",
            episode_id="S01E01",
            recap_mode="MAIN_STORIES",
            output_count=3,
            available_recap_modes=["FULL_EPISODE", "MAIN_STORIES"],
            output_counts_by_mode={"FULL_EPISODE": 1, "MAIN_STORIES": 3},
        )
        app = SimpleNamespace(
            mode_var=Mock(get=Mock(return_value="Recap cả tập")),
            _mode_code=Mock(return_value="FULL_EPISODE"),
            tree=Mock(selection=Mock(return_value=())),
            projects=[record],
            project_store=Mock(),
            _refresh_table=Mock(),
            _log=Mock(),
        )
        RecapStudioApp._mode_changed(app)
        self.assertEqual(record.recap_mode, "FULL_EPISODE")
        self.assertEqual(record.output_count, 1)
        app.project_store.save.assert_called_once_with([record])
