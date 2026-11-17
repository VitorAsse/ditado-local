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


def audio_endpoints(flow):
    """Active Windows endpoints as [(id, name)], flow 'render' or 'capture'."""
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities

    data_flow = EDataFlow.eRender if flow == "render" else EDataFlow.eCapture
    devices = AudioUtilities.GetAllDevices(
        data_flow=data_flow.value,
        device_state=DEVICE_STATE.ACTIVE.value,
    )
    return sorted(
        ((device.id, device.FriendlyName) for device in devices if device.FriendlyName),
        key=lambda item: item[1].casefold(),
    )


def audio_endpoint_names(flow):
    try:
        return [name for _id, name in audio_endpoints(flow)]
    except Exception:
        return []


def capture_endpoint_signature():
    """Microphones Windows reports, read without probing drivers; None if unknown."""
    try:
        return tuple(sorted(device_id for device_id, _name in audio_endpoints("capture")))
    except Exception:
        return None


def set_default_endpoint(flow, name):
    """Make the named endpoint the Windows default for every role.

    Returns False when no active endpoint has that name.
    """
    from pycaw.constants import EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities

    device_id = next((item_id for item_id, item_name in audio_endpoints(flow) if item_name == name), None)
    if device_id is None:
        return False
    data_flow = EDataFlow.eRender if flow == "render" else EDataFlow.eCapture
    enumerator = AudioUtilities.GetDeviceEnumerator()
    for role_name in PLAYBACK_ROLE_NAMES:
        role = getattr(ERole, role_name)
        try:
            current = enumerator.GetDefaultAudioEndpoint(data_flow.value, role.value).GetId()
        except Exception:
            current = ""
        if str(current).casefold() != str(device_id).casefold():
            AudioUtilities.SetDefaultDevice(device_id, roles=[role])
    return True


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
