"""Micrófono de Windows: listar, activar o silenciar y cambiar el volumen (el del Panel de control > Sonido).

Mismo enfoque que el control de micrófono de Visión (spec ``control-microfono-windows``): Core Audio de
Windows (``IMMDeviceEnumerator`` + ``IAudioEndpointVolume``) por ``ctypes``, sin paquetes nuevos (``pycaw`` no
está en el repositorio interno de pip de Ford). Es código propio de este proyecto, no depende de Visión.

Reglas (las mismas que valida Visión en la PC de la persona):
- Solo micrófonos CONECTADOS. Uno sin control de volumen (algunos drivers) sale con ``muted``/``volume`` en
  ``None`` («desconocido»), nunca como silenciado.
- ``set_mute`` solo cambia el silencio: no toca el volumen ni cuál es el micrófono predeterminado.
- ``set_volume``: de 1 a 100 fija el volumen Y quita el silencio; 0 SILENCIA y deja el volumen como estaba
  (Windows no silencia solo al llegar a 0), así que «Activar» después devuelve el volumen anterior.
- Toda escritura se RELEE; si Windows no la aplicó, se avisa (nunca se afirma algo sin verificar).
- Nada se toca por iniciativa propia: solo lo llaman acciones explícitas de la persona.

Los errores son ``MicControlError`` con un texto para personas; el detalle técnico va al registro.
"""

from __future__ import annotations

import ctypes
import logging
import re
import sys
import threading
import unicodedata
from ctypes import POINTER, byref, c_float, c_int, c_uint, c_void_p, wintypes
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


class MicControlError(Exception):
    """No se pudo leer o cambiar el micrófono; el texto sirve para mostrarlo a la persona."""


class MicUnsupported(MicControlError):
    """Este sistema no es Windows (o no tiene Core Audio)."""


@dataclass(frozen=True)
class Microphone:
    id: str
    name: str
    is_default: bool
    muted: bool | None  # None = el driver no deja leerlo
    volume: int | None  # 0-100 (el del dispositivo, aunque esté silenciado)


class MicBackend(Protocol):
    def list_microphones(self) -> list[Microphone]: ...

    def set_mute(self, mic_id: str, muted: bool) -> None: ...

    def set_volume(self, mic_id: str, scalar: float) -> None: ...


# -- Core Audio por ctypes (solo Windows) -------------------------------------------------------------
_E_CAPTURE, _E_CONSOLE, _STATE_ACTIVE, _CLSCTX_ALL, _VT_LPWSTR = 1, 0, 1, 23, 31
_RPC_E_CHANGED_MODE = -2147417850
_HRESULT = ctypes.c_long


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        import uuid

        v = uuid.UUID(text)
        return cls(v.time_low, v.time_mid, v.time_hi_version, (ctypes.c_ubyte * 8)(*v.bytes[8:]))


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _PropVariant(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("r1", ctypes.c_ushort),
        ("r2", ctypes.c_ushort),
        ("r3", ctypes.c_ushort),
        ("ptr", c_void_p),
        ("pad", c_void_p),
    ]


def _call(ptr, index: int, *argtypes):
    """Método ``index`` de la vtable de un puntero COM, ya enlazado a ``ptr``."""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    function = ctypes.WINFUNCTYPE(_HRESULT, c_void_p, *argtypes)(vtbl[index])
    return lambda *args: function(ptr, *args)


def _check(hresult: int, what: str) -> None:
    if hresult < 0:
        raise OSError(f"{what} falló: HRESULT 0x{hresult & 0xFFFFFFFF:08X}")


def _release(ptr) -> None:
    if ptr and ptr.value:
        _call(ptr, 2)()  # IUnknown::Release


class _Com:
    """Inicia COM en el hilo que llama y lo cierra al salir."""

    def __enter__(self) -> _Com:
        hresult = ctypes.WinDLL("ole32").CoInitializeEx(None, 0)
        self._owns = hresult >= 0
        if hresult < 0 and hresult != _RPC_E_CHANGED_MODE:
            raise OSError(f"CoInitializeEx falló: HRESULT 0x{hresult & 0xFFFFFFFF:08X}")
        return self

    def __exit__(self, *exc) -> None:
        if self._owns:
            ctypes.WinDLL("ole32").CoUninitialize()


class WindowsCoreAudio:
    """Implementación real. Cada llamada abre y cierra COM y libera todo lo que pide."""

    _lock = threading.Lock()  # un solo pedido a la vez al hardware

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise MicUnsupported("Controlar el micrófono solo está disponible en Windows.")

    @staticmethod
    def _enumerator():
        enumerator = c_void_p()
        clsid = _GUID.parse("{BCDE0395-E52F-467C-8E3D-C4579291692E}")  # MMDeviceEnumerator
        iid = _GUID.parse("{A95664D2-9614-4F35-A746-DE8DB63617E6}")  # IMMDeviceEnumerator
        _check(
            ctypes.WinDLL("ole32").CoCreateInstance(byref(clsid), None, 1, byref(iid), byref(enumerator)),
            "CoCreateInstance",
        )
        return enumerator

    @staticmethod
    def _device_id(device) -> str:
        raw = c_void_p()
        _check(_call(device, 5, POINTER(c_void_p))(byref(raw)), "GetId")
        try:
            return ctypes.wstring_at(raw.value) if raw.value else ""
        finally:
            ctypes.WinDLL("ole32").CoTaskMemFree(raw)

    @staticmethod
    def _device_name(device) -> str:
        store = c_void_p()
        _check(_call(device, 4, c_uint, POINTER(c_void_p))(0, byref(store)), "OpenPropertyStore")
        try:
            key = _PropertyKey(_GUID.parse("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)  # nombre amigable
            value = _PropVariant()
            _check(
                _call(store, 5, POINTER(_PropertyKey), POINTER(_PropVariant))(byref(key), byref(value)),
                "GetValue",
            )
            try:
                return ctypes.wstring_at(value.ptr) if value.vt == _VT_LPWSTR and value.ptr else ""
            finally:
                ctypes.WinDLL("ole32").PropVariantClear(byref(value))
        finally:
            _release(store)

    @staticmethod
    def _endpoint_volume(device):
        """``IAudioEndpointVolume`` del dispositivo, o ``None`` si el driver no expone control de volumen."""
        volume = c_void_p()
        iid = _GUID.parse("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        hresult = _call(device, 3, POINTER(_GUID), c_uint, c_void_p, POINTER(c_void_p))(
            byref(iid), _CLSCTX_ALL, None, byref(volume)
        )
        return volume if hresult >= 0 and volume.value else None

    @staticmethod
    def _read(volume) -> tuple[bool, float]:
        muted, level = wintypes.BOOL(), c_float()
        _check(_call(volume, 15, POINTER(wintypes.BOOL))(byref(muted)), "GetMute")
        _check(_call(volume, 9, POINTER(c_float))(byref(level)), "GetMasterVolumeLevelScalar")
        return bool(muted.value), float(level.value)

    def list_microphones(self) -> list[Microphone]:
        with self._lock, _Com():
            enumerator = self._enumerator()
            try:
                default_id, default = "", c_void_p()
                got = _call(enumerator, 4, c_int, c_int, POINTER(c_void_p))(
                    _E_CAPTURE, _E_CONSOLE, byref(default)
                )
                if got >= 0 and default.value:
                    try:
                        default_id = self._device_id(default)
                    finally:
                        _release(default)
                collection = c_void_p()
                _check(
                    _call(enumerator, 3, c_int, c_uint, POINTER(c_void_p))(
                        _E_CAPTURE, _STATE_ACTIVE, byref(collection)
                    ),
                    "EnumAudioEndpoints",
                )
                try:
                    count = c_uint()
                    _check(_call(collection, 3, POINTER(c_uint))(byref(count)), "GetCount")
                    mics: list[Microphone] = []
                    for index in range(count.value):
                        device = c_void_p()
                        _check(_call(collection, 4, c_uint, POINTER(c_void_p))(index, byref(device)), "Item")
                        try:
                            mic_id = self._device_id(device)
                            volume = self._endpoint_volume(device)
                            muted: bool | None = None
                            percent: int | None = None
                            if volume is not None:
                                try:
                                    is_muted, scalar = self._read(volume)
                                    muted, percent = is_muted, round(scalar * 100)
                                except OSError:
                                    log.warning("No se pudo leer el estado de %s", mic_id, exc_info=True)
                                finally:
                                    _release(volume)
                            mics.append(
                                Microphone(
                                    mic_id,
                                    self._device_name(device) or "Micrófono",
                                    mic_id == default_id,
                                    muted,
                                    percent,
                                )
                            )
                        finally:
                            _release(device)
                    return mics
                finally:
                    _release(collection)
            finally:
                _release(enumerator)

    def _with_volume(self, mic_id: str, action) -> None:
        with self._lock, _Com():
            enumerator = self._enumerator()
            try:
                device = c_void_p()
                _check(
                    _call(enumerator, 5, ctypes.c_wchar_p, POINTER(c_void_p))(mic_id, byref(device)),
                    "GetDevice",
                )
                try:
                    volume = self._endpoint_volume(device)
                    if volume is None:
                        raise MicControlError(
                            "Este micrófono no deja cambiar su volumen desde aquí. "
                            "Usa el Panel de control de Windows."
                        )
                    try:
                        action(volume)
                    finally:
                        _release(volume)
                finally:
                    _release(device)
            finally:
                _release(enumerator)

    def set_mute(self, mic_id: str, muted: bool) -> None:
        self._with_volume(
            mic_id, lambda v: _check(_call(v, 14, wintypes.BOOL, c_void_p)(muted, None), "SetMute")
        )

    def set_volume(self, mic_id: str, scalar: float) -> None:
        self._with_volume(
            mic_id,
            lambda v: _check(
                _call(v, 7, c_float, c_void_p)(c_float(scalar), None), "SetMasterVolumeLevelScalar"
            ),
        )


# -- API del módulo -----------------------------------------------------------------------------------
_backend: MicBackend | None = None


def set_backend(backend: MicBackend | None) -> None:
    """Para las pruebas: instala un backend falso (``None`` vuelve al real)."""
    global _backend
    _backend = backend


def _get_backend() -> MicBackend:
    global _backend
    if _backend is None:
        _backend = WindowsCoreAudio()
    return _backend


def _friendly(exc: Exception, what: str) -> MicControlError:
    if isinstance(exc, MicControlError):
        return exc
    log.warning("Falló el audio de Windows al %s: %s", what, exc, exc_info=True)
    return MicControlError(f"Windows no dejó {what}. Prueba desde el Panel de control > Sonido > Grabación.")


def list_microphones() -> list[Microphone]:
    """Micrófonos conectados: el predeterminado primero y luego por nombre."""
    try:
        mics = _get_backend().list_microphones()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo del sistema de audio es un mensaje para la persona
        raise _friendly(exc, "leer los micrófonos") from exc
    return sorted(mics, key=lambda m: (not m.is_default, m.name.lower()))


def _find(mic_id: str) -> Microphone:
    for mic in list_microphones():
        if mic.id == mic_id:
            return mic
    raise MicControlError("Ese micrófono ya no está conectado. Elige otro de la lista.")


def set_mute(mic_id: str, muted: bool) -> Microphone:
    """Silencia o activa el micrófono SIN tocar su volumen. Devuelve el estado releído."""
    _find(mic_id)
    try:
        _get_backend().set_mute(mic_id, muted)
    except Exception as exc:  # noqa: BLE001
        raise _friendly(exc, "silenciar el micrófono" if muted else "activar el micrófono") from exc
    after = _find(mic_id)
    if after.muted is not muted:
        raise MicControlError(
            "Windows no aplicó el cambio en este micrófono. "
            "Prueba desde el Panel de control > Sonido > Grabación."
        )
    return after


def set_volume(mic_id: str, percent: int) -> Microphone:
    """Volumen de 0 a 100: de 1 a 100 fija el nivel y quita el silencio; 0 SILENCIA y deja el nivel."""
    level = max(0, min(100, int(percent)))
    _find(mic_id)
    try:
        backend = _get_backend()
        if level == 0:
            backend.set_mute(mic_id, True)
        else:
            backend.set_volume(mic_id, level / 100)
            backend.set_mute(mic_id, False)
    except Exception as exc:  # noqa: BLE001
        raise _friendly(exc, "cambiar el volumen") from exc
    after = _find(mic_id)
    if after.muted is not (level == 0) or (
        level > 0 and after.volume is not None and abs(after.volume - level) > 1
    ):
        raise MicControlError(
            "Windows no aplicó el cambio de volumen en este micrófono. "
            "Prueba desde el Panel de control > Sonido > Grabación."
        )
    return after


def _plain(text: str) -> str:
    s = unicodedata.normalize("NFKD", text.casefold())
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def match_device(name: str, mics: list[Microphone]) -> Microphone | None:
    """El micrófono de Windows que corresponde al dispositivo de audio elegido en la aplicación (por nombre).

    PortAudio recorta los nombres (31 caracteres en MME); se compara sin tildes ni mayúsculas y, si no hay
    coincidencia exacta, por prefijo o por contener el nombre. Sin nombre: el predeterminado.
    """
    if not mics:
        return None
    wanted = _plain(name or "")
    if not wanted:
        return next((m for m in mics if m.is_default), mics[0])
    exact = [m for m in mics if _plain(m.name) == wanted]
    if exact:
        return exact[0]
    loose = [
        m
        for m in mics
        if _plain(m.name).startswith(wanted) or wanted.startswith(_plain(m.name)) or wanted in _plain(m.name)
    ]
    return loose[0] if loose else None
