"""Desktop local API lifecycle tests."""
from unittest.mock import MagicMock

import run


def test_start_desktop_local_api_wires_shared_service_and_recording(monkeypatch):
    api = MagicMock()
    service = MagicMock()
    controller = MagicMock()
    server = MagicMock()
    factory = MagicMock(return_value=server)
    monkeypatch.setattr(run._app_module, "_service", lambda: service)
    monkeypatch.setattr(run, "DesktopRecordingController", lambda value: controller)
    monkeypatch.setattr(run, "LocalAPIServer", factory)

    result = run._start_desktop_local_api(api)

    factory.assert_called_once_with(
        service,
        mode="desktop",
        app_data_dir=run._app_module.APP_DATA_DIR,
        recording=controller,
    )
    server.start.assert_called_once()
    assert result is server


def test_start_desktop_local_api_does_not_block_gui_on_service_conflict(monkeypatch):
    monkeypatch.setattr(run._app_module, "_service", MagicMock())
    server = MagicMock()
    server.start.side_effect = run.LocalAPIError("service_already_running", "busy", 409)
    monkeypatch.setattr(run, "LocalAPIServer", MagicMock(return_value=server))

    assert run._start_desktop_local_api(MagicMock()) is None
