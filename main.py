#!/usr/bin/env python3
import ctypes
import os
import subprocess
import time
import logging
import click as click
import cv2
import mss
import numpy as np
import win32api
import win32con
import win32gui

user32 = ctypes.windll.user32


class System:
    def __init__(self, browser: str = None, vortex: bool = False, verbose: bool = False, force_primary: bool = False):

        logging.info("Initializing system")
        logging.info(f"Arguments: browser={browser}, vortex={vortex}, verbose={verbose}")

        self.monitors = self.get_monitors()
        if force_primary:
            self.monitors = [self.monitors[0]]
        else:
            self.monitors = sorted(self.monitors, key=lambda chunk: chunk[0])
        logging.info(f"Found {len(self.monitors)} monitors")
        logging.info(f"Monitors: {self.monitors}")

        self.vortex_btn, self.web_btn, self.click_btn, self.understood_btn, self.staging_btn = self._load_assets()
        logging.info("Loaded assets")

        # Check if there are displays left of the primary display which makes their coordinates negative
        self.negative_displays = [m for m in self.monitors if m[0] < 0]
        logging.info(f"Found {len(self.negative_displays)} negative displays")
        logging.info(f"Negative displays: {self.negative_displays}")

        self.negative_offset_x = sum([m[0] for m in self.negative_displays])
        self.negative_offset_y = sorted(self.monitors, key=lambda monitor: monitor[1])[0][1]

        self.biggest_display = sorted(self.monitors, key=lambda monitor: abs(monitor[0]))[-1]
        logging.info(f"Biggest display: {self.biggest_display}")
        logging.info("Calculated offsets")

        self.sift, self.vortex_desc, self.web_desc, self.click_desc, self.understood_desc, \
        self.staging_desc, self.matcher = self._init_detector()
        logging.info("Initialized detector")

        self.screen, self.v_monitor = self._init_screen_capture()

        if browser:
            self.prep_browser(browser.lower())
        if vortex:
            self.prep_vortex()

        self.vortex = vortex
        self.verbose = verbose

    def _init_detector(self):
        logging.info("Initializing detector")
        sift = cv2.SIFT_create()

        _, vortex_descriptors = sift.detectAndCompute(self.vortex_btn, mask=None)
        _, website_descriptors = sift.detectAndCompute(self.web_btn, mask=None)
        _, click_descriptors = sift.detectAndCompute(self.click_btn, mask=None)
        _, understood_descriptors = sift.detectAndCompute(self.understood_btn, mask=None)
        _, staging_descriptors = sift.detectAndCompute(self.staging_btn, mask=None)
        logging.info("Initialized descriptors")

        matcher = cv2.BFMatcher()

        return sift, vortex_descriptors, website_descriptors, click_descriptors, understood_descriptors, \
               staging_descriptors, matcher

    def _init_screen_capture(self) -> (mss.mss, dict):
        screen = mss.mss()
        mon = screen.monitors[0]

        aspect_ratio = self.monitors[0][2] / self.monitors[0][3] if not self.negative_displays \
            else self.monitors[-1][2] / self.monitors[-1][3]
        monitor = {
            "top": sorted(self.monitors, key=lambda chunk: chunk[1])[0][1],
            "left": sorted(self.monitors, key=lambda chunk: chunk[0])[0][0],
            "width": sum([abs(m[2]) for m in self.monitors]),
            "height": abs(int(self.biggest_display[0] * (aspect_ratio ** -1))) if len(self.monitors) > 1 else
            self.monitors[0][3],
            "mon": 0,
        }
        logging.info(f"Initialized screen capture with monitor: {monitor}")

        return screen, monitor

    @staticmethod
    def _load_assets() -> (np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray):
        vortex_path = "assets/VortexDownloadButton.png"
        web_path = "assets/WebsiteDownloadButton.png"
        click_path = "assets/ClickHereButton.png"
        understood_path = "assets/UnderstoodButton.png"
        staging_path = "assets/StagingButton.png"

        for path in [vortex_path, web_path, click_path, understood_path, staging_path]:
            if os.path.isfile(path):
                yield cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)
            else:
                raise FileNotFoundError(f"Asset {path} not found")

    def capture_screen(self) -> np.ndarray:
        img = np.array(self.screen.grab(self.v_monitor))
        logging.info("Captured screen")

        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    @staticmethod
    def click(x, y):
        o_pos = win32api.GetCursorPos()

        win32api.SetCursorPos((x, y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
        logging.info(f"Clicked at ({x}, {y})")

        win32api.SetCursorPos(o_pos)

    def detect(self, img, descriptors, threshold, bbox=None) -> tuple[int, int]:
        screenshot_keypoints, screenshot_desc = self.sift.detectAndCompute(img, mask=None)

        matches = self.matcher.knnMatch(descriptors, screenshot_desc, k=2)

        points = np.array([screenshot_keypoints[m.trainIdx].pt for m, _ in matches if m.distance < threshold]).astype(
            np.int32)

        if bbox:
            points = np.array([p for p in points if bbox[0] < p[0] < bbox[2] and bbox[1] < p[1] < bbox[3]])

        point = np.median(points, axis=0)
        if not np.isnan(point).any():
            return self.img_coords_to_mon_coords(int(point[0]), int(point[1]))

    @staticmethod
    def get_monitors() -> list[tuple[int, int, int, int]]:
        return [monitor[2] for monitor in win32api.EnumDisplayMonitors(None, None)]

    @staticmethod
    def get_vortex_bbox() -> list[int, int, int, int]:
        vortex = user32.FindWindowW(None, u"Vortex")
        bbox = list(win32gui.GetWindowRect(vortex))
        logging.info(f"Vortex bbox: {bbox}")

        return bbox

    def img_coords_to_mon_coords(self, pos_x, pos_y) -> tuple[int, int]:
        if len(self.monitors) > 1:
            click_x = self.negative_offset_x + pos_x
            click_y = self.negative_offset_y + pos_y
        else:
            click_x = pos_x
            click_y = pos_y

        return click_x, click_y

    def mon_coords_to_img_coords(self, pos_x, pos_y) -> tuple[int, int]:
        if len(self.monitors) > 1:
            click_x = pos_x - self.negative_offset_x
            click_y = pos_y
        else:
            click_x = pos_x
            click_y = pos_y

        return click_x, click_y

    def prep_browser(self, browser):
        commands = {"chrome": r'start chrome about:blank', "firefox": r'start firefox'}
        win_name = {"chrome": "about:blank - Google Chrome", "firefox": "Mozilla Firefox"}

        if browser not in commands.keys():
            raise ValueError(f"Browser \'{browser}\' not supported")

        subprocess.Popen(commands[browser], shell=True)
        time.sleep(0.4)
        h_browser = user32.FindWindowW(None, win_name[browser])

        logging.info("Found Firefox window")

        if len(self.monitors) > 1:
            user32.ShowWindow(h_browser, 1)
            x_b, y_b, w_b, h_b = self.monitors[0][0], self.monitors[0][1], self.monitors[0][2], self.monitors[0][3]
            win32gui.SetWindowPos(h_browser, None, x_b, y_b, w_b, h_b, True)
            user32.ShowWindow(h_browser, 3)
        logging.info("Moved chrome window")

    def prep_vortex(self):
        vortex = user32.FindWindowW(None, u"Vortex")
        logging.info("Found vortex window")

        if len(self.monitors) > 1:
            user32.ShowWindow(vortex, 1)
            x_v, y_v, w_v, h_v = self.monitors[1][0], self.monitors[1][1], self.monitors[1][2], self.monitors[1][3]
            win32gui.SetWindowPos(vortex, None, x_v, y_v, w_v, h_v, True)
            user32.ShowWindow(vortex, 3)
        logging.info("Moved vortex window")

    def scan(self):
        v_found = False
        w_found = False
        web_loop = 0

        while True:
            img = self.capture_screen()

            if not v_found and self.vortex:
                vortex_bbox = list(self.get_vortex_bbox())
                fac = 5 + (5 - vortex_bbox[0] / 512)
                # Pad borders to ignore possible mismatches
                vortex_bbox[0] += vortex_bbox[2] * (1 / fac)
                vortex_bbox[1] += vortex_bbox[3] * (1 / fac)
                vortex_bbox[2] -= vortex_bbox[2] * (1 / fac)
                vortex_bbox[3] -= vortex_bbox[3] * (1 / fac)

                vortex_bbox[0], vortex_bbox[1] = self.mon_coords_to_img_coords(vortex_bbox[0], vortex_bbox[1])
                vortex_bbox[2], vortex_bbox[3] = self.mon_coords_to_img_coords(vortex_bbox[2], vortex_bbox[3])

                vortex_loc = self.detect(img, self.vortex_desc, 100, vortex_bbox)
                understood_btn_loc = self.detect(img, self.understood_desc, 80)
                staging_btn_loc = self.detect(img, self.staging_desc, 80)

                if staging_btn_loc:
                    logging.info(f"Staging button found at {staging_btn_loc}. Clicking...")
                    self.click(staging_btn_loc[0], staging_btn_loc[1])
                    time.sleep(1)

                elif understood_btn_loc:
                    logging.info(f"Understood button found at {understood_btn_loc}. Clicking...")
                    self.click(understood_btn_loc[0], understood_btn_loc[1])
                    time.sleep(1)

                if vortex_loc:
                    logging.info(f"Found vortex button at {vortex_loc}")
                    self.click(vortex_loc[0], vortex_loc[1])
                    v_found = True

            elif w_found:
                click_loc = self.detect(img, self.click_desc, 80)

                if click_loc:
                    logging.info(f"Found click button at {click_loc}")
                    w_found = False
                    v_found = False
                    time.sleep(3)

            elif v_found or not self.vortex:
                web_loc = self.detect(img, self.web_desc, 100)

                if web_loc:
                    logging.info(f"Found web button at {web_loc}")
                    self.click(web_loc[0], web_loc[1])

                    web_loop = 0

                    if self.vortex:
                        w_found = True

                elif web_loop > 5:
                    logging.info("Web button not found. Restarting...")
                    v_found = False
                    web_loop = 0
                else:
                    web_loop += 1

            logging.info("Waiting 2 seconds")
            time.sleep(2)


@click.command()
@click.option('--browser', is_flag=False, default=None, help='Specifies browser to automatically move next to Vortex. '
                                                             'Only works with --vortex. Supported browsers: chrome, '
                                                             'firefox')
@click.option('--vortex', is_flag=True, default=False, help='Enables vortex mode')
@click.option('--verbose', is_flag=True, default=False, help='Enables verbose mode')
@click.option('--force-primary', is_flag=True, default=False, help='Forces the script to use the primary monitor')
def main(browser, vortex, verbose, force_primary):
    assert browser in ["chrome", "firefox", None], f"Browser \'{browser}\' not supported"
    assert browser and vortex or not browser and not vortex, "Browser and vortex must be used together"

    if verbose:
        logging.basicConfig(level=logging.INFO, handlers=[
            logging.FileHandler("log.log"),
            logging.StreamHandler()
        ], format='[%(asctime)s - %(levelname)s] in %(funcName)s: %(message)s')
    else:
        logging.basicConfig(handlers=[
            logging.FileHandler("log.log"),
            logging.StreamHandler()
        ], format='[%(asctime)s - %(levelname)s]: %(message)s', level=logging.ERROR)

    agent = System(browser, vortex, verbose, force_primary)
    agent.scan()


if __name__ == "__main__":
    main()
