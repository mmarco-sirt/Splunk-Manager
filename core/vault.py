"""
Cifrado de credenciales con DPAPI de Windows (la misma protección que usa el Administrador de credenciales).

Los valores cifrados se guardan en config.py como "dpapi:<base64>". Solo los puede descifrar el mismo usuario de
Windows (en el mismo equipo o, en un dominio con perfil móvil, en otro equipo del dominio con ese usuario).
"""
import base64
import sys

PREFIX = "dpapi:"
_ENTROPY = b"SplunkManager/credenciales/v1"
_DESCRIPTION = "Splunk Manager"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class VaultError(Exception):
    """No se pudo cifrar o descifrar una credencial."""


def available():
    return sys.platform == "win32"


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


if available():
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _blob_args = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                  wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptProtectData.argtypes = _blob_args
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob(data):
        buffer = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    def _take(blob):
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))

    def _protect(data):
        data_in, _keep_data = _blob(data)
        entropy, _keep_entropy = _blob(_ENTROPY)
        out = _Blob()
        if not _crypt32.CryptProtectData(ctypes.byref(data_in), _DESCRIPTION, ctypes.byref(entropy), None, None,
                                         _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise VaultError(f"Windows no pudo cifrar la credencial (error {ctypes.get_last_error()}).")
        return _take(out)

    def _unprotect(data):
        data_in, _keep_data = _blob(data)
        entropy, _keep_entropy = _blob(_ENTROPY)
        out = _Blob()
        if not _crypt32.CryptUnprotectData(ctypes.byref(data_in), None, ctypes.byref(entropy), None, None,
                                           _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise VaultError("No se puede descifrar: la credencial se guardó con otro usuario de Windows o en otro "
                             "equipo. Vuelva a introducirla.")
        return _take(out)


def encrypt(text):
    """Cifra 'text' y devuelve "dpapi:<base64>". Un valor ya cifrado se devuelve tal cual."""
    if is_encrypted(text):
        return text
    if not available():
        raise VaultError("El cifrado de credenciales (DPAPI) solo está disponible en Windows.")
    return PREFIX + base64.b64encode(_protect(text.encode("utf-8"))).decode("ascii")


def decrypt(value):
    """Descifra un valor "dpapi:<base64>". Un valor sin cifrar se devuelve tal cual."""
    if not is_encrypted(value):
        return value
    if not available():
        raise VaultError("Esta credencial está cifrada con DPAPI de Windows y solo se puede usar en Windows.")
    try:
        raw = base64.b64decode(value[len(PREFIX):], validate=True)
    except ValueError:
        raise VaultError("La credencial cifrada está dañada. Vuelva a introducirla.")
    return _unprotect(raw).decode("utf-8")
