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
    def __init__(self, chrome: bool = False, vortex: bool = False, verbose: bool = False):

        logging.info("Initializing system")
        logging.info(f"Arguments: chrome={chrome}, vortex={vortex}, verbose={verbose}")

        self.monitors = self.getMonitors()
        logging.info(f"Found {len(self.monitors)} monitors")
        logging.info(f"Monitors: {self.monitors}")

        self.vortex_btn, self.web_btn, self.click_btn, self.understood_btn = self._load_assets()
        logging.info("Loaded assets")

        self.negative_displays = [m for m in self.monitors if m[0] < 0]
        logging.info(f"Found {len(self.negative_displays)} negative displays")
        logging.info(f"Negative displays: {self.negative_displays}")

        self.negative_offset_x = sum([m[0] for m in self.negative_displays])
        self.negative_offset_y = sorted(self.monitors, key=lambda monitor: monitor[1])[0][1]
        self.biggest_display = sorted(self.monitors, key=lambda monitor: abs(monitor[0]))[-1]
        logging.info("Calculated offsets")

        self.sift, self.vortex_desc, self.web_desc, self.click_desc, self.understood_desc, self.matcher = self.init_detector()
        logging.info("Initialized detector")

        self.screen, self.v_monitor = self.init_screen_capture()

        if chrome:
            self.prep_chrome()

        self.vortex = vortex
        self.verbose = verbose

    def init_screen_capture(self):
        screen = mss.mss()
        mon = screen.monitors[0]

        monitor = {
            "top": mon["top"],
            "left": mon["left"],
            "width": mon["width"],
            "height": abs(int(self.biggest_display[0] * (9 / 16))),
            "mon": 0,
        }
        logging.info(f"Initialized screen capture with monitor: {monitor}")

        return screen, monitor

    def captureScreen(self):
        img = np.array(self.screen.grab(self.v_monitor))
        logging.info("Captured screen")

        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    @staticmethod
    def getMonitors():
        return sorted([monitor[2] for monitor in win32api.EnumDisplayMonitors(None, None)], key=lambda chunk: chunk[0])

    @staticmethod
    def _load_assets():
        vortex_path = "assets/VortexDownloadButton.png"
        web_path = "assets/WebsiteDownloadButton.png"
        click_path = "assets/ClickHereButton.png"
        understood_path = "assets/UnderstoodButton.png"
        for path in [vortex_path, web_path, click_path, understood_path]:
            if os.path.isfile(path):
                yield cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)
            else:
                raise FileNotFoundError(f"Asset {path} not found")

    def generate_click(self, pos_x, pos_y):
        if len(self.monitors) > 1:
            click_x = self.negative_offset_x + pos_x
            click_y = self.negative_offset_y + pos_y
        else:
            click_x = pos_x
            click_y = pos_y

        return click_x, click_y

    def monitor_to_image(self, pos_x, pos_y):
        if len(self.monitors) > 1:
            click_x = pos_x - self.negative_offset_x
            click_y = pos_y
        else:
            click_x = pos_x
            click_y = pos_y

        return click_x, click_y

    def init_detector(self):
        logging.info("Initializing detector")
        sift = cv2.SIFT_create()

        _, vortex_descriptors = sift.detectAndCompute(self.vortex_btn, mask=None)
        _, website_descriptors = sift.detectAndCompute(self.web_btn, mask=None)
        _, click_descriptors = sift.detectAndCompute(self.click_btn, mask=None)
        _, understood_descriptors = sift.detectAndCompute(self.understood_btn, mask=None)
        logging.info("Initialized descriptors")

        matcher = cv2.BFMatcher()

        return sift, vortex_descriptors, website_descriptors, click_descriptors, understood_descriptors, matcher

    def detect(self, img, descriptors, threshold, bbox=None):
        screenshot_keypoints, screenshot_desc = self.sift.detectAndCompute(img, mask=None)

        matches = self.matcher.knnMatch(descriptors, screenshot_desc, k=2)

        points = np.array([screenshot_keypoints[m.trainIdx].pt for m, _ in matches if m.distance < threshold]).astype(
            np.int32)
        if bbox:
            points = np.array([p for p in points if bbox[0] < p[0] < bbox[2] and bbox[1] < p[1] < bbox[3]])
        point = np.median(points, axis=0)
        if not np.isnan(point).any():
            print(points)
            cv2.imwrite("test.png", cv2.rectangle(img, (int(point[0] - 10), int(point[1] - 10)), (int(point[0] + 10), int(point[1] + 10)), (0, 255, 0), 2))

            return self.generate_click(int(point[0]), int(point[1]))

    def scan(self):
        v_found = False
        web_loop = 0
        vortex_loop = 0
        w_found = False
        while True:
            img = self.captureScreen()
            if not v_found and self.vortex:
                vortex_bbox = list(self.get_vortex_bbox())
                vortex_bbox[0], vortex_bbox[1] = self.monitor_to_image(vortex_bbox[0], vortex_bbox[1])
                vortex_bbox[2], vortex_bbox[3] = self.monitor_to_image(vortex_bbox[2], vortex_bbox[3])
                vortex_loc = self.detect(img, self.vortex_desc, 80, vortex_bbox)
                understood_btn_loc = self.detect(img, self.understood_desc, 80)
                print(understood_btn_loc)
                if understood_btn_loc:
                    self.click(understood_btn_loc[0], understood_btn_loc[1])
                    time.sleep(1)
                if vortex_loc and vortex_loop <=3:
                    logging.info(f"Found vortex button at {vortex_loc}")
                    self.click(vortex_loc[0], vortex_loc[1])
                    v_found = True
            elif w_found:
                click_loc = self.detect(img, self.click_desc, 40)
                if click_loc:
                    logging.info(f"Found click button at {click_loc}")
                    vortex_loop = 0
                    w_found = False
                    v_found = False
                    time.sleep(3)
            elif v_found or not self.vortex:
                web_loc = self.detect(img, self.web_desc, 40)
                if web_loc:
                    logging.info(f"Found web button at {web_loc}")
                    self.click(web_loc[0], web_loc[1])
                    web_loop = 0
                    if self.vortex:
                        w_found = True
                elif web_loop > 5:
                    v_found = False
                    web_loop = 0
                else:
                    web_loop += 1
            logging.info("Waiting 2 seconds")
            time.sleep(2)

    def get_vortex_bbox(self):
        vortex = user32.FindWindowW(None, u"Vortex")
        bbox = list(win32gui.GetWindowRect(vortex))
        bbox[0] += bbox[2] * (1 / 5)
        bbox[1] += bbox[3] * (1 / 5)
        bbox[2] -= bbox[2] * (1 / 5)
        bbox[3] -= bbox[3] * (1 / 5)

        return bbox

    @staticmethod
    def click(x, y):
        o_pos = win32api.GetCursorPos()

        win32api.SetCursorPos((x, y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
        logging.info(f"Clicked at ({x}, {y})")

        win32api.SetCursorPos(o_pos)

    def prep_chrome(self):

        subprocess.Popen(r'start chrome /new-tab about:blank', shell=True)
        logging.info("Opened chrome")

        time.sleep(0.4)

        chrome = user32.FindWindowW(None, u"about:blank - Google Chrome")
        vortex = user32.FindWindowW(None, u"Vortex")
        user32.ShowWindow(chrome, 1)
        user32.ShowWindow(vortex, 1)
        logging.info("Found chrome and vortex windows")

        if len(self.monitors) > 1:
            x_c, y_c, w_c, h_c = self.monitors[0][0], self.monitors[0][1], self.monitors[0][2], self.monitors[0][3]
            x_v, y_v, w_v, h_v = self.monitors[1][0], self.monitors[1][1], self.monitors[1][2], self.monitors[1][3]
        else:
            x_c, y_c, w_c, h_c = 0, 0, self.monitors[0][2] / 2, self.monitors[0][3] / 2
            x_v, y_v, w_v, h_v = self.monitors[0][2] / 2, self.monitors[0][3] / 2, self.monitors[0][2], \
                                 self.monitors[0][3]

        win32gui.SetWindowPos(chrome, None, x_c, y_c, 500, 500, True)
        win32gui.SetWindowPos(vortex, None, x_v, y_v, 500, 500, True)
        user32.ShowWindow(chrome, 3)
        user32.ShowWindow(vortex, 3)
        logging.info("Moved chrome and vortex windows")


@click.command()
@click.option('--chrome', is_flag=True, default=True, help='Automatically move and size chrome and vortex windows')
@click.option('--vortex', is_flag=True, default=True, help='Enables vortex mode')
@click.option('--verbose', is_flag=True, default=True, help='Enables verbose mode')
def main(chrome, vortex, verbose):
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

    agent = System(chrome, vortex, verbose)
    agent.scan()


if __name__ == "__main__":
    main()
