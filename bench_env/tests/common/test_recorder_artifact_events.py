from __future__ import annotations

from bench_env.env.recorder import RunRecorder


class _Sink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def test_recorder_emits_artifact_created_after_file_exists(tmp_path):
    sink = _Sink()
    recorder = RunRecorder(tmp_path, save_trajectory=True, event_sink=sink)
    run_dir = recorder.start_run(agent="agent", model_name="model")
    episode = recorder.start_episode(
        task_id="fake.Task",
        task_name="Fake task",
        trial_id=0,
    )

    episode.finish({"task_id": "fake.Task", "is_success": True})

    artifact_events = [
        event for event in sink.events if event.type == "artifact.created"
    ]
    assert artifact_events
    trajectory_event = next(
        event
        for event in artifact_events
        if event.payload["relative_path"].endswith("/trajectory.json")
    )
    artifact_path = run_dir / trajectory_event.payload["relative_path"]
    assert artifact_path.exists()
    assert trajectory_event.task_id == "fake.Task"
    assert trajectory_event.payload["kind"] == "json"
    assert trajectory_event.payload["size_bytes"] == artifact_path.stat().st_size
