from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

import autostart
from defaults import WEB_PORT


user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
HICON = getattr(wintypes, "HICON", wintypes.HANDLE)
HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
HBRUSH = getattr(wintypes, "HBRUSH", wintypes.HANDLE)
HGDIOBJ = getattr(wintypes, "HGDIOBJ", wintypes.HANDLE)
ATOM = getattr(wintypes, "ATOM", wintypes.WORD)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.SendMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.SendMessageW.restype = LRESULT
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = HCURSOR
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE,
    wintypes.LPCWSTR,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.MessageBoxW.argtypes = [
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.UINT,
]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = HGDIOBJ
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.ExtractIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT]
shell32.ExtractIconW.restype = HICON

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_SETFONT = 0x0030
WM_SYSCOMMAND = 0x0112
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
SC_MINIMIZE = 0xF020
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BST_CHECKED = 1
BST_UNCHECKED = 0

WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_TABSTOP = 0x00010000
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_THICKFRAME = 0x00040000
WS_MAXIMIZEBOX = 0x00010000
BS_PUSHBUTTON = 0x00000000
BS_AUTOCHECKBOX = 0x00000003
SS_LEFT = 0x00000000
SW_SHOW = 5
SW_HIDE = 0
SW_RESTORE = 9
DEFAULT_GUI_FONT = 17
COLOR_WINDOW = 5
IDC_ARROW = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
TRAY_UID = 1

ID_EXIT = 1001
ID_AUTOSTART = 1002

TITLE = "BS1 \u63a7\u5236\u5668"
CLASS_NAME = "BS1LightControllerWindow"
PORT_LABEL = f"web\u63a7\u5236\u754c\u9762\u7aef\u53e3\uff1a{WEB_PORT}"
AUTOSTART_LABEL = "\u5f00\u673a\u81ea\u542f"
EXIT_LABEL = "\u9000\u51fa\u7a0b\u5e8f"
AUTOSTART_ERROR = "\u5f00\u673a\u81ea\u542f\u8bbe\u7f6e\u5931\u8d25"


class LocalWindow:
    def __init__(self, controller, web_server):
        self.controller = controller
        self.web_server = web_server
        self.hinstance = kernel32.GetModuleHandleW(None)
        self.hwnd = None
        self.checkbox = None
        self.exiting = False
        self.hicon = None
        self.tray_added = False
        self._wndproc = WNDPROC(self._window_proc)
        self._registered = False

    def run(self) -> None:
        self.hicon = self._load_icon()
        self._register_class()
        self._create_window()
        self._add_tray_icon()
        self.hide_window()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def shutdown(self) -> None:
        if self.exiting:
            return
        self.exiting = True
        try:
            self._remove_tray_icon()
            self.web_server.stop()
        finally:
            self.controller.stop()
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)

    def show_window(self) -> None:
        if self.hwnd:
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            user32.UpdateWindow(self.hwnd)

    def hide_window(self) -> None:
        if self.hwnd:
            user32.ShowWindow(self.hwnd, SW_HIDE)

    def _register_class(self) -> None:
        if self._registered:
            return
        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = self.hinstance
        wndclass.hIcon = self.hicon
        wndclass.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wndclass.hbrBackground = ctypes.cast(COLOR_WINDOW + 1, HBRUSH)
        wndclass.lpszClassName = CLASS_NAME
        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if not atom and ctypes.get_last_error() != 1410:
            raise ctypes.WinError(ctypes.get_last_error())
        self._registered = True

    def _create_window(self) -> None:
        style = WS_OVERLAPPEDWINDOW & ~WS_THICKFRAME & ~WS_MAXIMIZEBOX
        self.hwnd = user32.CreateWindowExW(
            0,
            CLASS_NAME,
            TITLE,
            style,
            420,
            260,
            360,
            145,
            None,
            None,
            self.hinstance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._create_controls()

    def _create_controls(self) -> None:
        font = gdi32.GetStockObject(DEFAULT_GUI_FONT)
        self._control("STATIC", PORT_LABEL, WS_CHILD | WS_VISIBLE | SS_LEFT, 18, 18, 300, 24, 0, font)
        self.checkbox = self._control(
            "BUTTON",
            AUTOSTART_LABEL,
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX,
            18,
            58,
            120,
            26,
            ID_AUTOSTART,
            font,
        )
        if bool(self.controller.get_config().get("autostart")):
            user32.SendMessageW(self.checkbox, BM_SETCHECK, BST_CHECKED, 0)
        self._control(
            "BUTTON",
            EXIT_LABEL,
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
            210,
            56,
            110,
            30,
            ID_EXIT,
            font,
        )

    def _control(self, class_name, text, style, x, y, width, height, control_id, font):
        hwnd = user32.CreateWindowExW(
            0,
            class_name,
            text,
            style,
            x,
            y,
            width,
            height,
            self.hwnd,
            wintypes.HMENU(control_id),
            self.hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        user32.SendMessageW(hwnd, WM_SETFONT, font, True)
        return hwnd

    def _toggle_autostart(self) -> None:
        checked = user32.SendMessageW(self.checkbox, BM_GETCHECK, 0, 0) == BST_CHECKED
        try:
            autostart.set_autostart(checked)
            self.controller.update_config({"autostart": checked})
        except Exception as exc:
            fallback = BST_UNCHECKED if checked else BST_CHECKED
            user32.SendMessageW(self.checkbox, BM_SETCHECK, fallback, 0)
            user32.MessageBoxW(self.hwnd, str(exc), AUTOSTART_ERROR, 0x10)

    def _load_icon(self):
        if getattr(sys, "frozen", False):
            icon = shell32.ExtractIconW(self.hinstance, sys.executable, 0)
            if icon:
                return icon
        icon_path = Path(__file__).resolve().parent / "assets" / "thrm.ico"
        if icon_path.exists():
            icon = user32.LoadImageW(
                None,
                str(icon_path),
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if icon:
                return HICON(icon)
        return None

    def _tray_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = TRAY_UID
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAYICON
        data.hIcon = self.hicon
        data.szTip = TITLE
        return data

    def _add_tray_icon(self) -> None:
        if self.tray_added or not self.hwnd:
            return
        data = self._tray_data()
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.tray_added = True

    def _remove_tray_icon(self) -> None:
        if not self.tray_added or not self.hwnd:
            return
        data = self._tray_data()
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        self.tray_added = False

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_COMMAND:
            control_id = int(wparam) & 0xFFFF
            if control_id == ID_EXIT:
                self.shutdown()
                return 0
            if control_id == ID_AUTOSTART:
                self._toggle_autostart()
                return 0
        if msg == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) == SC_MINIMIZE:
            self.hide_window()
            return 0
        if msg == WM_TRAYICON:
            event = int(lparam)
            if event == WM_LBUTTONDBLCLK:
                self.show_window()
                return 0
            if event in {WM_RBUTTONDOWN, WM_RBUTTONUP, WM_CONTEXTMENU}:
                return 0
        if msg == WM_CLOSE:
            self.hide_window()
            return 0
        if msg == WM_DESTROY:
            self._remove_tray_icon()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
