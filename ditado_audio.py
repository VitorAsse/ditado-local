def default_endpoint_factory():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


class PlaybackMuteController:
    def __init__(self, endpoint_factory=None):
        self.endpoint_factory = endpoint_factory or default_endpoint_factory
        self.endpoint = None
        self.previously_muted = None
        self.active = False

    def mute_for_recording(self):
        if self.active:
            return True

        try:
            self.endpoint = self.endpoint_factory()
            self.previously_muted = bool(self.endpoint.GetMute())
            self.endpoint.SetMute(True, None)
            self.active = True
            return True
        except Exception:
            self.endpoint = None
            self.previously_muted = None
            self.active = False
            return False

    def restore(self):
        if not self.active:
            return True

        try:
            self.endpoint.SetMute(self.previously_muted, None)
            return True
        except Exception:
            return False
        finally:
            self.endpoint = None
            self.previously_muted = None
            self.active = False
