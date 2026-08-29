def default_endpoint_factory():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


PLAYBACK_ROLE_NAMES = ("eConsole", "eMultimedia", "eCommunications")


def default_playback_devices_factory():
    from pycaw.constants import EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities

    enumerator = AudioUtilities.GetDeviceEnumerator()
    devices = {}
    for role_name in PLAYBACK_ROLE_NAMES:
        role = getattr(ERole, role_name)
        try:
            device = enumerator.GetDefaultAudioEndpoint(
                EDataFlow.eRender.value,
                role.value,
            )
            devices[role_name] = device.GetId()
        except Exception:
            continue
    return devices


def default_playback_devices_restorer(devices):
    from pycaw.constants import ERole
    from pycaw.pycaw import AudioUtilities

    current_devices = default_playback_devices_factory()
    failures = []
    for role_name, device_id in devices.items():
        current_id = str(current_devices.get(role_name, ""))
        if current_id.casefold() == str(device_id).casefold():
            continue
        try:
            AudioUtilities.SetDefaultDevice(
                device_id,
                roles=[getattr(ERole, role_name)],
            )
        except Exception as error:
            failures.append(error)
    if failures:
        raise failures[0]


class PlaybackMuteController:
    def __init__(
        self,
        endpoint_factory=None,
        playback_devices_factory=None,
        playback_devices_restorer=None,
    ):
        self.endpoint_factory = endpoint_factory or default_endpoint_factory
        if playback_devices_factory is None:
            playback_devices_factory = (
                default_playback_devices_factory
                if endpoint_factory is None
                else lambda: {}
            )
        self.playback_devices_factory = playback_devices_factory
        self.playback_devices_restorer = (
            playback_devices_restorer or default_playback_devices_restorer
        )
        self.endpoint = None
        self.previously_muted = None
        self.playback_devices = {}
        self.recent_playback_devices = {}
        self.active = False

    def mute_for_recording(self):
        if self.active:
            return True

        try:
            self.recent_playback_devices = {}
            self.playback_devices = self.playback_devices_factory()
            self.endpoint = self.endpoint_factory()
            self.previously_muted = bool(self.endpoint.GetMute())
            self.endpoint.SetMute(True, None)
            self.active = True
            return True
        except Exception:
            self.endpoint = None
            self.previously_muted = None
            self.playback_devices = {}
            self.active = False
            return False

    def restore(self):
        if not self.active:
            return True

        restored = True
        try:
            if self.playback_devices:
                self.playback_devices_restorer(self.playback_devices)
        except Exception:
            restored = False
        try:
            self.endpoint.SetMute(self.previously_muted, None)
        except Exception:
            restored = False
        finally:
            self.recent_playback_devices = dict(self.playback_devices)
            self.endpoint = None
            self.previously_muted = None
            self.playback_devices = {}
            self.active = False
        return restored

    def reassert_defaults(self):
        if self.active or not self.recent_playback_devices:
            return True

        devices = self.recent_playback_devices
        self.recent_playback_devices = {}
        try:
            self.playback_devices_restorer(devices)
            return True
        except Exception:
            return False
