"""Graceful Windows single-instance handoff for the switchboard UI."""

import ctypes


class InstanceGate:
    def __init__(self, name='Local\\ClaudeSessionSwitch_UI'):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = ctypes.c_void_p
        kernel.CreateMutexW.argtypes = (handle, ctypes.c_int, ctypes.c_wchar_p)
        kernel.CreateMutexW.restype = handle
        kernel.CreateEventW.argtypes = (handle, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
        kernel.CreateEventW.restype = handle
        kernel.SetEvent.argtypes = (handle,)
        kernel.SetEvent.restype = ctypes.c_int
        kernel.ResetEvent.argtypes = (handle,)
        kernel.ResetEvent.restype = ctypes.c_int
        kernel.WaitForSingleObject.argtypes = (handle, ctypes.c_ulong)
        kernel.WaitForSingleObject.restype = ctypes.c_ulong
        kernel.ReleaseMutex.argtypes = (handle,)
        kernel.ReleaseMutex.restype = ctypes.c_int
        kernel.CloseHandle.argtypes = (handle,)
        kernel.CloseHandle.restype = ctypes.c_int
        self.kernel = kernel
        self.name = name
        self.mutex = None
        self.event = None
        self.owned = False

    def enter(self, timeout_ms=8000):
        if self.mutex:
            raise RuntimeError('Instance gate already entered')
        self.mutex = self.kernel.CreateMutexW(None, True, self.name)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        existed = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        self.owned = not existed
        self.event = self.kernel.CreateEventW(None, True, False, self.name + '_replace')
        if not self.event:
            error = ctypes.get_last_error()
            self.release()
            raise ctypes.WinError(error)
        if existed:
            if not self.kernel.SetEvent(self.event):
                error = ctypes.get_last_error()
                self.release()
                raise ctypes.WinError(error)
            state = self.kernel.WaitForSingleObject(self.mutex, timeout_ms)
            if state not in (0, 0x80):  # WAIT_OBJECT_0 or WAIT_ABANDONED
                self.kernel.ResetEvent(self.event)
                self.release()
                return False
            self.owned = True
        self.kernel.ResetEvent(self.event)
        return True

    def replacement_requested(self):
        return bool(self.owned and self.event and
                    self.kernel.WaitForSingleObject(self.event, 0) == 0)

    def release(self):
        if self.owned and self.mutex:
            self.kernel.ReleaseMutex(self.mutex)
        self.owned = False
        if self.event:
            self.kernel.CloseHandle(self.event)
            self.event = None
        if self.mutex:
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None
