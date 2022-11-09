import subprocess
import time

import ctypes
import win32api, win32con
import numpy as np
import cv2
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
import os
import mss

class System:
    def __init__(self, chrome: bool=False, vortex: bool=False):
        self.monitors = self.getMonitors()
        self.vortex_btn, self.web_btn = self._load_assets()

    @staticmethod
    def captureScreen():
        with mss.mss() as sct:
            mon = sct.monitors[0]
            monitor = {
                "top": mon["top"],
                "left": mon["left"],
                "width": mon["width"],
                "height": mon["height"],
                "mon": 0,
            }
            img = np.array(sct.grab(monitor))
            return img
    @staticmethod
    def getMonitors():
        return [monitor[2] for monitor in win32api.EnumDisplayMonitors(None, None)]
    def _load_assets(self):
        vortex_path = "assets/VortexDownloadButton.png"
        web_path = "assets/WebsiteDownloadButton.png"
        if os.path.isfile(vortex_path) and os.path.isfile(web_path):
            return cv2.imread(vortex_path), cv2.imread(web_path)
        else:
            raise FileNotFoundError("Assets not found. Please verify installation")


    def click(self, x, y):
        o_pos = win32api.GetCursorPos()
        win32api.SetCursorPos((x, y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
        win32api.SetCursorPos(o_pos)


def moveWindows(monitors):
    subprocess.Popen(r'start chrome /new-tab about:blank', shell=False)
    time.sleep(0.4)

    chrome = user32.FindWindowW(None, u"about:blank - Google Chrome")
    vortex = user32.FindWindowW(None, u"Vortex")
    user32.ShowWindow(chrome, 1)
    user32.ShowWindow(vortex, 1)
    if len(monitors) > 1:
        x_c, y_c, w_c, h_c = monitors[0][0], monitors[0][1], monitors[0][2], monitors[0][3]
        x_v, y_v, w_v, h_v = monitors[1][0], monitors[1][1], monitors[1][2], monitors[1][3]
    else:
        x_c, y_c, w_c, h_c = 0, 0, monitors[0][2] / 2, monitors[0][3] / 2
        x_v, y_v, w_v, h_v = monitors[0][2] / 2, monitors[0][3] / 2, monitors[0][2], monitors[0][3]
    user32.moveWindow(chrome, x_c, y_c, w_c, h_c, True)
    user32.moveWindow(vortex, x_v, y_v, w_v, h_v, True)
