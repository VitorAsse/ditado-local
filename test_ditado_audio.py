import unittest

from ditado_audio import CaptureEndpointWatcher, PlaybackMuteController


class FakeEndpointVolume:
    def __init__(self, muted=False):
        self.muted = bool(muted)
        self.set_calls = []

    def GetMute(self):
        return int(self.muted)

    def SetMute(self, muted, _event_context):
        self.muted = bool(muted)
        self.set_calls.append(self.muted)


class RestoreFailingEndpointVolume(FakeEndpointVolume):
    def SetMute(self, muted, event_context):
        if self.set_calls:
            raise RuntimeError("Audio endpoint unavailable")
        super().SetMute(muted, event_context)


class PlaybackMuteControllerTests(unittest.TestCase):
    def test_mutes_during_recording_and_restores_unmuted_state(self):
        endpoint = FakeEndpointVolume(muted=False)
        controller = PlaybackMuteController(lambda: endpoint)

        self.assertTrue(controller.mute_for_recording())
        self.assertTrue(endpoint.muted)

        controller.restore()

        self.assertFalse(endpoint.muted)
        self.assertEqual([True, False], endpoint.set_calls)

    def test_preserves_an_already_muted_state(self):
        endpoint = FakeEndpointVolume(muted=True)
        controller = PlaybackMuteController(lambda: endpoint)

        self.assertTrue(controller.mute_for_recording())
        controller.restore()

        self.assertTrue(endpoint.muted)
        self.assertEqual([True, True], endpoint.set_calls)

    def test_audio_control_failure_does_not_block_recording(self):
        def unavailable_endpoint():
            raise RuntimeError("Audio endpoint unavailable")

        controller = PlaybackMuteController(unavailable_endpoint)

        self.assertFalse(controller.mute_for_recording())
        controller.restore()

    def test_restore_failure_does_not_block_the_app(self):
        endpoint = RestoreFailingEndpointVolume(muted=False)
        controller = PlaybackMuteController(lambda: endpoint)

        self.assertTrue(controller.mute_for_recording())
        self.assertFalse(controller.restore())
        self.assertFalse(controller.active)

    def test_restores_the_output_roles_captured_before_recording(self):
        endpoint = FakeEndpointVolume(muted=False)
        restored = []
        original_devices = {
            "eConsole": "device-a",
            "eMultimedia": "device-a",
            "eCommunications": "device-b",
        }
        controller = PlaybackMuteController(
            lambda: endpoint,
            lambda: dict(original_devices),
            lambda devices: restored.append(dict(devices)),
        )

        self.assertTrue(controller.mute_for_recording())
        self.assertTrue(controller.restore())
        self.assertEqual([original_devices], restored)

        self.assertTrue(controller.reassert_defaults())
        self.assertEqual([original_devices, original_devices], restored)
        self.assertTrue(controller.reassert_defaults())
        self.assertEqual([original_devices, original_devices], restored)

    def test_new_recording_cancels_a_pending_default_reassertion(self):
        endpoint = FakeEndpointVolume(muted=False)
        restored = []
        controller = PlaybackMuteController(
            lambda: endpoint,
            lambda: {"eMultimedia": "device-a"},
            lambda devices: restored.append(dict(devices)),
        )

        controller.mute_for_recording()
        controller.restore()
        controller.mute_for_recording()

        self.assertTrue(controller.reassert_defaults())
        self.assertEqual([{"eMultimedia": "device-a"}], restored)


class CaptureEndpointWatcherTests(unittest.TestCase):
    def test_flags_changes_only_after_registration(self):
        callbacks = []
        unregistered = []

        def register(on_change):
            callbacks.append(on_change)
            return lambda: unregistered.append(True)

        watcher = CaptureEndpointWatcher(register=register)
        self.assertTrue(watcher.needs_refresh())
        self.assertTrue(watcher.start())
        self.assertTrue(watcher.needs_refresh())  # first recording still refreshes
        watcher.consume()
        self.assertFalse(watcher.needs_refresh())
        callbacks[0]()
        self.assertTrue(watcher.needs_refresh())
        watcher.stop()
        watcher._thread.join(2)
        self.assertEqual([True], unregistered)
        self.assertTrue(watcher.needs_refresh())

    def test_registration_failure_keeps_always_refreshing(self):
        def register(_on_change):
            raise OSError("sem servico de audio")

        watcher = CaptureEndpointWatcher(register=register)
        self.assertFalse(watcher.start())
        watcher.consume()
        self.assertTrue(watcher.needs_refresh())


if __name__ == "__main__":
    unittest.main()
