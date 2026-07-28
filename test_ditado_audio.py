import unittest

from ditado_audio import PlaybackMuteController


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


if __name__ == "__main__":
    unittest.main()
