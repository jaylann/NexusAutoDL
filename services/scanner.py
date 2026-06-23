"""
Main scanning orchestrator with state machine.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import numpy.typing as npt

from models import (
    AppConfig,
    BoundingBox,
    ButtonType,
    DetectionResult,
    Monitor,
    ScanState,
    ScanStatus,
)
from services.button_detector import ButtonDetector
from services.click_controller import ClickController
from services.debug_recorder import DebugRecorder
from services.screen_capture import ScreenCapture
from services.window_manager import WindowManager
from utils.logger import get_logger

logger = get_logger(__name__)

VORTEX_WEB_RETRY_LIMIT = 3  # Attempts before falling back to Vortex search


class Scanner:
    """Orchestrates the scanning and clicking process."""

    def __init__(
        self,
        config: AppConfig,
        monitors: list[Monitor],
        status_callback: Optional[Callable[[ScanStatus], None]] = None,
    ) -> None:
        """
        Initialize scanner.

        Args:
            config: Application configuration
            monitors: List of monitors
            status_callback: Optional callback for status updates
        """
        self.config = config
        self.monitors = monitors
        self.status_callback = status_callback

        # Initialize components
        self.screen_capture = ScreenCapture(monitors, config.force_primary)
        self.button_detector = ButtonDetector(use_legacy_buttons=self.config.legacy)
        self.window_manager = WindowManager(monitors)
        self.click_controller = ClickController()
        debug_path = (
            Path(self.config.debug_frame_dir) if self.config.debug_frame_dir else None
        )
        self.debug_recorder = DebugRecorder(debug_path)

        # Initialize status
        self.status = ScanStatus(current_action="Initialized")

        # Setup windows if needed
        self._setup_windows()

        logger.info("Scanner initialized")

    def _setup_windows(self) -> None:
        """Setup browser and Vortex windows."""
        try:
            if self.config.browser:
                self.window_manager.launch_browser(self.config.browser)
                self.status.current_action = f"Launched {self.config.browser.value}"
                self._update_status()

            if self.config.vortex:
                self.window_manager.position_vortex()
                self.status.current_action = "Positioned Vortex"
                self._update_status()

            if self.config.window_title:
                self.window_manager.position_window_by_title(self.config.window_title)
                self.status.current_action = (
                    f"Positioned window: {self.config.window_title}"
                )
                self._update_status()
        except Exception as e:
            error_msg = f"Window setup error: {e}"
            logger.error(error_msg)
            self.status.errors.append(error_msg)
            self._update_status()

    def _update_status(self) -> None:
        """Update status and trigger callback."""
        if self.status_callback:
            self.status_callback(self.status)

    def _click_detection(self, detection: DetectionResult) -> None:
        """
        Click on a detection result.

        Args:
            detection: Detection to click
        """
        # Convert image coords to monitor coords
        mon_x: int
        mon_y: int
        mon_x, mon_y = self.screen_capture.img_coords_to_monitor_coords(
            detection.x, detection.y
        )

        self.click_controller.click(mon_x, mon_y)
        self.status.clicks_count += 1
        self.status.detections.append(detection)
        self._update_status()

    def _handle_vortex_state(
        self,
        img: npt.NDArray[np.uint8],
        iteration: int,
    ) -> bool:
        """
        Handle Vortex button detection state.

        Args:
            img: Current screenshot

        Returns:
            True if Vortex button found and clicked
        """
        # Get Vortex window bbox
        vortex_bbox = self.window_manager.get_vortex_bbox()
        if not vortex_bbox:
            self.status.current_action = "Waiting for Vortex window..."
            self._update_status()
            return False

        # Convert to image coordinates and pad
        img_x1: int
        img_y1: int
        img_x1, img_y1 = self.screen_capture.monitor_coords_to_img_coords(
            vortex_bbox.x1, vortex_bbox.y1
        )
        img_x2: int
        img_y2: int
        img_x2, img_y2 = self.screen_capture.monitor_coords_to_img_coords(
            vortex_bbox.x2, vortex_bbox.y2
        )

        bbox: BoundingBox = BoundingBox(x1=img_x1, y1=img_y1, x2=img_x2, y2=img_y2)

        # Calculate padding factor
        fac: float = 5 + (5 - vortex_bbox.x1 / 512)
        padded_bbox: BoundingBox = bbox.pad(1 / fac)

        # Check for popup dialogs first (legacy workflow only)
        if self.config.legacy:
            popup_buttons: list[tuple[ButtonType, str]] = [
                (ButtonType.UNDERSTOOD, "Clicking 'Understood' button"),
                (ButtonType.STAGING, "Clicking 'Staging' button"),
            ]
            for button_type, action in popup_buttons:
                detection: Optional[DetectionResult] = self.button_detector.detect(
                    img,
                    button_type,
                    min_matches=6,
                    ratio=self.config.ratio_threshold,
                )
                if detection:
                    self.status.state = ScanState.HANDLING_POPUP
                    self.status.current_action = action
                    self._update_status()
                    self.debug_recorder.record(
                        img, detection, iteration, f"popup_{button_type.value}"
                    )
                    self._click_detection(detection)
                    time.sleep(self.config.retry_delay)
                    return False

        # Detect Vortex download button
        vortex_detection: Optional[DetectionResult] = self.button_detector.detect(
            img,
            ButtonType.VORTEX,
            min_matches=self.config.min_matches,
            ratio=self.config.ratio_threshold,
            bbox=padded_bbox,
        )

        if vortex_detection:
            self.status.current_action = "Clicking Vortex download button"
            self._update_status()
            self.debug_recorder.record(
                img, vortex_detection, iteration, "vortex_download"
            )
            self._click_detection(vortex_detection)
            return True

        self.status.current_action = "Searching for Vortex button..."
        self._update_status()
        return False

    def _handle_web_state(
        self,
        img: npt.NDArray[np.uint8],
        iteration: int,
    ) -> tuple[bool, bool]:
        """
        Handle web download button detection state.

        Args:
            img: Current screenshot

        Returns:
            Tuple of (clicked_web_button, should_reset_to_vortex)
        """
        targets: list[tuple[ButtonType, str]] = [
            (ButtonType.WEBSITE, "website download button")
        ]

        if not self.config.vortex:
            targets.append((ButtonType.WABBAJACK, "Wabbajack download button"))

        for button_type, label in targets:
            detection: Optional[DetectionResult] = self.button_detector.detect(
                img,
                button_type,
                min_matches=6,
                ratio=self.config.ratio_threshold,
            )

            if detection:
                self.status.current_action = f"Clicking {label}"
                self._update_status()
                self.debug_recorder.record(
                    img, detection, iteration, f"web_{button_type.value}"
                )
                self._click_detection(detection)
                self.status.web_retry_count = 0
                return True, False

        retry_limit = (
            VORTEX_WEB_RETRY_LIMIT
            if self.config.vortex
            else self.config.wabbajack_retry_limit
        )

        if self.status.web_retry_count >= retry_limit:
            if self.config.vortex:
                logger.info(
                    "Web button not found after %s attempts, returning to Vortex scan",
                    retry_limit,
                )
                self.status.current_action = "Rechecking Vortex (web button missing)"
                self.status.web_retry_count = 0
                self._update_status()
                return False, True

            logger.info("Web button not found, restarting...")
            self.status.current_action = "Restarting (button not found)"
            self.status.web_retry_count = 0
            self._update_status()
            return False, False

        self.status.web_retry_count += 1
        target_text = " or ".join(label for _, label in targets)
        self.status.current_action = (
            f"Searching for {target_text}... "
            f"(attempt {self.status.web_retry_count}/{retry_limit})"
        )
        self._update_status()
        return False, False

    def _handle_click_dialog_state(
        self,
        img: npt.NDArray[np.uint8],
        iteration: int,
    ) -> bool:
        """
        Handle click dialog detection state.

        Args:
            img: Current screenshot

        Returns:
            True if dialog found and clicked
        """
        if not self.config.legacy:
            self.status.current_action = "Skipping legacy click dialog"
            self._update_status()
            return True

        click_detection: Optional[DetectionResult] = self.button_detector.detect(
            img, ButtonType.CLICK, min_matches=6, ratio=self.config.ratio_threshold
        )

        if click_detection:
            self.status.current_action = "Clicking dialog button"
            self._update_status()
            self.debug_recorder.record(img, click_detection, iteration, "click_dialog")
            self._click_detection(click_detection)
            time.sleep(3)  # Wait for dialog to process
            return True

        self.status.current_action = "Waiting for click dialog..."
        self._update_status()
        return False

    def scan_loop(self, max_iterations: Optional[int] = None) -> None:
        """
        Main scanning loop.

        Args:
            max_iterations: Optional maximum iterations (for testing)
        """
        self.status.state = (
            ScanState.WAITING_FOR_VORTEX
            if self.config.vortex
            else ScanState.WAITING_FOR_WEB
        )
        self._update_status()

        vortex_found: bool = False
        web_clicked: bool = False
        iteration: int = 0

        try:
            while True:
                if max_iterations and iteration >= max_iterations:
                    break
                iteration += 1

                # Capture screen
                img = self.screen_capture.capture()

                # State machine
                if not vortex_found and self.config.vortex:
                    self.status.state = ScanState.WAITING_FOR_VORTEX
                    vortex_found = self._handle_vortex_state(img, iteration)
                    if vortex_found:
                        self.status.state = ScanState.VORTEX_CLICKED
                        self._update_status()

                elif web_clicked and self.config.vortex:
                    self.status.state = ScanState.WEB_CLICKED
                    dialog_complete = self._handle_click_dialog_state(img, iteration)
                    if dialog_complete:
                        # Reset for next mod
                        vortex_found = False
                        web_clicked = False
                        self.status.state = ScanState.WAITING_FOR_VORTEX
                        self._update_status()

                elif vortex_found or not self.config.vortex:
                    self.status.state = ScanState.WAITING_FOR_WEB
                    web_clicked_result, reset_to_vortex = self._handle_web_state(
                        img, iteration
                    )
                    if web_clicked_result:
                        web_clicked = True
                    if reset_to_vortex and self.config.vortex:
                        vortex_found = False
                        web_clicked = False
                        self.status.state = ScanState.WAITING_FOR_VORTEX
                        self._update_status()
                    if web_clicked and not self.config.vortex:
                        # Non-Vortex mode: reset immediately
                        vortex_found = False
                        web_clicked = False

                # Wait before next iteration
                time.sleep(self.config.click_delay)

        except KeyboardInterrupt:
            self.status.current_action = "Stopped by user"
            self.status.state = ScanState.IDLE
            self._update_status()
            logger.info("Scan stopped by user")
        except Exception as e:
            error_msg = f"Scanner error: {e}"
            logger.error(error_msg, exc_info=True)
            self.status.errors.append(error_msg)
            self.status.state = ScanState.ERROR
            self._update_status()
            raise
