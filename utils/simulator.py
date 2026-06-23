"""
Simulation mode for testing without Windows or actual buttons.
Generates fake detections and runs the full TUI interface.
"""

from __future__ import annotations

import asyncio
from loguru import logger
import random
import time
from typing import Callable, Optional

from models import (
    AppConfig,
    ScanStatus,
    ScanState,
    ButtonType,
    DetectionResult,
    Monitor,
    BoundingBox,
)


class SimulatedScanner:
    """Simulates the scanner with fake detections."""

    def __init__(
        self,
        config: AppConfig,
        status_callback: Optional[Callable[[ScanStatus], None]] = None,
    ):
        """Initialize simulator."""
        self.config = config
        self.status_callback = status_callback
        self.status = ScanStatus(current_action="Simulation initialized")
        self.running = False

        logger.info("Simulator initialized")

    def _update_status(self) -> None:
        """Update status and trigger callback."""
        if self.status_callback:
            self.status_callback(self.status)

    def _simulate_detection(self, button_type: ButtonType) -> DetectionResult:
        """Create a fake detection."""
        x = random.randint(200, 1600)
        y = random.randint(200, 900)
        confidence = random.uniform(0.7, 0.98)
        num_matches = random.randint(8, 25)

        return DetectionResult(
            button_type=button_type,
            x=x,
            y=y,
            confidence=confidence,
            num_matches=num_matches,
        )

    async def scan_loop_async(self, max_iterations: Optional[int] = None) -> None:
        """Async scanning loop for Textual integration."""
        self.running = True
        iteration = 0

        # Simulation scenarios
        scenarios = [
            # Vortex mode scenario
            (
                ScanState.WAITING_FOR_VORTEX,
                "Scanning for Vortex button...",
                ButtonType.VORTEX,
                3,
            ),
            (
                ScanState.HANDLING_POPUP,
                "Found Understood popup",
                ButtonType.UNDERSTOOD,
                2,
            ),
            (ScanState.VORTEX_CLICKED, "Vortex button clicked", None, 1),
            (
                ScanState.WAITING_FOR_WEB,
                "Scanning for download button...",
                ButtonType.WABBAJACK,
                4,
            ),
            (ScanState.WEB_CLICKED, "Download button clicked", None, 1),
            (ScanState.WAITING_FOR_VORTEX, "Waiting for next mod...", None, 2),
        ]

        scenario_idx = 0

        try:
            while self.running:
                if max_iterations and iteration >= max_iterations:
                    break

                iteration += 1

                # Get current scenario
                state, action, button, wait_cycles = scenarios[
                    scenario_idx % len(scenarios)
                ]

                self.status.state = state
                self.status.current_action = action
                self._update_status()

                # Simulate finding and clicking button
                if button:
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                    # Simulate detection
                    detection = self._simulate_detection(button)
                    self.status.detections.append(detection)
                    self.status.clicks_count += 1
                    self.status.current_action = f"Clicked {button.value} button at ({detection.x}, {detection.y})"

                    logger.info(f"[SIM] Detected and clicked {button.value}")
                    self._update_status()

                # Wait before next scenario
                await asyncio.sleep(self.config.click_delay * wait_cycles)

                # Randomly inject errors
                if random.random() < 0.05:  # 5% chance
                    error = random.choice(
                        [
                            "Simulated timeout waiting for button",
                            "Simulated window not found",
                            "Simulated detection confidence too low",
                        ]
                    )
                    self.status.errors.append(error)
                    logger.warning(f"[SIM] {error}")
                    self._update_status()

                scenario_idx += 1

        except asyncio.CancelledError:
            self.status.current_action = "Simulation stopped"
            self.status.state = ScanState.IDLE
            self._update_status()
            logger.info("Simulation cancelled")
        except Exception as e:
            error_msg = f"Simulation error: {e}"
            logger.error(error_msg, exc_info=True)
            self.status.errors.append(error_msg)
            self.status.state = ScanState.ERROR
            self._update_status()

    def scan_loop(self, max_iterations: Optional[int] = None) -> None:
        """Synchronous scanning loop (for headless mode)."""
        self.running = True
        iteration = 0

        # Simplified sync version
        states = [
            (ScanState.WAITING_FOR_VORTEX, ButtonType.VORTEX),
            (ScanState.WAITING_FOR_WEB, ButtonType.WABBAJACK),
            (ScanState.HANDLING_POPUP, ButtonType.UNDERSTOOD),
        ]

        try:
            while self.running:
                if max_iterations and iteration >= max_iterations:
                    break

                iteration += 1
                state, button = states[iteration % len(states)]

                self.status.state = state
                self.status.current_action = f"Simulating {state.value}..."
                self._update_status()

                time.sleep(self.config.click_delay)

                # Simulate detection
                detection = self._simulate_detection(button)
                self.status.detections.append(detection)
                self.status.clicks_count += 1

                logger.info(
                    f"[SIM] Clicked {button.value} at ({detection.x}, {detection.y})"
                )
                self._update_status()

        except KeyboardInterrupt:
            self.status.current_action = "Simulation stopped by user"
            self.status.state = ScanState.IDLE
            self._update_status()
            logger.info("Simulation interrupted")


def get_simulated_monitors() -> list[Monitor]:
    """Get fake monitor configuration."""
    return [
        Monitor(x=0, y=0, width=1920, height=1080),  # Primary
        Monitor(x=1920, y=0, width=1920, height=1080),  # Secondary
    ]


class SimulatorWindowManager:
    """Mock window manager for simulation."""

    def __init__(self, monitors: list[Monitor]):
        self.monitors = monitors
        logger.info("Simulator window manager initialized")

    def launch_browser(self, browser):
        logger.info(f"[SIM] Would launch {browser.value}")

    def position_vortex(self):
        logger.info("[SIM] Would position Vortex window")

    def position_window_by_title(self, title: str):
        logger.info(f"[SIM] Would position window: {title}")

    def get_vortex_bbox(self) -> Optional[BoundingBox]:
        """Return fake Vortex bounding box."""
        return BoundingBox(x1=1920, y1=0, x2=3840, y2=1080)

    @staticmethod
    def get_all_monitors() -> list[Monitor]:
        """Get fake monitors."""
        return get_simulated_monitors()


class SimulatorClickController:
    """Mock click controller for simulation."""

    def __init__(self):
        logger.info("Simulator click controller initialized")

    def click(self, x: int, y: int, delay: float = 0.1):
        logger.info(f"[SIM] Would click at ({x}, {y})")

    def double_click(self, x: int, y: int, delay: float = 0.1):
        logger.info(f"[SIM] Would double-click at ({x}, {y})")

    def move_to(self, x: int, y: int):
        logger.debug(f"[SIM] Would move cursor to ({x}, {y})")
