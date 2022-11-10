import ctypes
import os
import subprocess
import time

import click as click
import cv2
import mss
import numpy as np
import win32api
import win32con

user32 = ctypes.windll.user32


class System:
    def __init__(self, chrome: bool = False, vortex: bool = False, verbose: bool = False):
        self.monitors = self.getMonitors()
        self.vortex_btn, self.web_btn = self._load_assets()
        self.negative_displays = [m for m in self.monitors if m[0] < 0]
        self.negative_offset_x = sum([m[0] for m in self.negative_displays])
        self.negative_offset_y = sorted(self.monitors, key=lambda monitor: monitor[1])[0][1]
        self.biggest_display = sorted(self.monitors, key=lambda monitor: abs(monitor[0]))[-1]
        self.sift, self.vortex_desc, self.web_desc, self.matcher = self.init_detector()
        if chrome:
            self.prep_chrome()
        self.vortex = vortex
        self.verbose = verbose

    def captureScreen(self):
        with mss.mss() as sct:
            mon = sct.monitors[0]
            monitor = {
                "top": mon["top"],
                "left": mon["left"],
                "width": mon["width"],
                "height": abs(int(self.biggest_display[0] * (9 / 16))),
                "mon": 0,
            }
            img = np.array(sct.grab(monitor))
            return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    @staticmethod
    def getMonitors():
        return sorted([monitor[2] for monitor in win32api.EnumDisplayMonitors(None, None)], key=lambda chunk: chunk[0])

    def _load_assets(self):
        vortex_path = "assets/VortexDownloadButton.png"
        web_path = "assets/WebsiteDownloadButton.png"
        if os.path.isfile(vortex_path) and os.path.isfile(web_path):
            return cv2.cvtColor(cv2.imread(vortex_path), cv2.COLOR_BGR2RGB), cv2.cvtColor(cv2.imread(web_path),
                                                                                          cv2.COLOR_BGR2RGB)
        else:
            raise FileNotFoundError("Assets not found. Please verify installation")

    def generate_click(self, pos_x, pos_y):
        if len(self.monitors) > 1:
            click_x = self.negative_offset_x + pos_x
            click_y = self.negative_offset_y + pos_y
        else:
            click_x = pos_x
            click_y = pos_y
        return click_x, click_y

    def init_detector(self):
        sift = cv2.SIFT_create()
        _, vortex_descriptors = sift.detectAndCompute(self.vortex_btn, mask=None)
        _, website_descriptors = sift.detectAndCompute(self.web_btn, mask=None)
        matcher = cv2.BFMatcher()
        return sift, vortex_descriptors, website_descriptors, matcher

    def detect(self, img, descriptors, threshold):
        screenshot_keypoints, screenshot_desc = self.sift.detectAndCompute(img, mask=None)

        matches = self.matcher.knnMatch(descriptors, screenshot_desc, k=2)
        points = np.array([screenshot_keypoints[m.trainIdx].pt for m, _ in matches if m.distance < threshold]).astype(
            np.int32)
        point = np.median(points, axis=0)
        if not np.isnan(point).any():
            return self.generate_click(int(point[0]), int(point[1]))

    def scan(self):
        v_found = False
        while True:
            img = self.captureScreen()
            if not v_found and self.vortex:
                vortex_loc = self.detect(img, self.vortex_desc, 40)
                if vortex_loc:
                    self.click(vortex_loc[0], vortex_loc[1])
                    v_found = True
            elif v_found or not self.vortex:
                web_loc = self.detect(img, self.web_desc, 40)
                if web_loc:
                    self.click(web_loc[0], web_loc[1])
                    v_found = False
                    time.sleep(5)
            time.sleep(2)

    @staticmethod
    def click(x, y):
        o_pos = win32api.GetCursorPos()
        win32api.SetCursorPos((x, y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
        win32api.SetCursorPos(o_pos)

    def prep_chrome(self):
        subprocess.Popen(r'start chrome /new-tab about:blank', shell=False)
        time.sleep(0.4)

        chrome = user32.FindWindowW(None, u"about:blank - Google Chrome")
        vortex = user32.FindWindowW(None, u"Vortex")
        user32.ShowWindow(chrome, 1)
        user32.ShowWindow(vortex, 1)
        if len(self.monitors) > 1:
            x_c, y_c, w_c, h_c = self.monitors[0][0], self.monitors[0][1], self.monitors[0][2], self.monitors[0][3]
            x_v, y_v, w_v, h_v = self.monitors[1][0], self.monitors[1][1], self.monitors[1][2], self.monitors[1][3]
        else:
            x_c, y_c, w_c, h_c = 0, 0, self.monitors[0][2] / 2, self.monitors[0][3] / 2
            x_v, y_v, w_v, h_v = self.monitors[0][2] / 2, self.monitors[0][3] / 2, self.monitors[0][2], \
                                 self.monitors[0][3]
        user32.moveWindow(chrome, x_c, y_c, w_c, h_c, True)
        user32.moveWindow(vortex, x_v, y_v, w_v, h_v, True)


@click.command()
@click.option('--chrome', is_flag=True, default=False, help='Automatically move and size chrome and vortex windows')
@click.option('--vortex', is_flag=True, default=False, help='Enables vortex mode')
@click.option('--verbose', is_flag=True, default=False, help='Enables verbose mode')
def main(chrome, vortex, verbose):
    agent = System(chrome, vortex, verbose)
    agent.scan()


if __name__ == "__main__":
    main()
