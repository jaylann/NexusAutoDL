#!/usr/bin/env python3
"""
Validation script to verify all modules load correctly.
Run this after installation to check the setup.
"""

import sys
from typing import List

# Ensure Unicode status glyphs print on Windows consoles (cp1252 by default).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def validate_imports() -> List[str]:
    """Validate all module imports."""
    errors = []

    print("Validating imports...")

    # Core modules
    try:
        import models  # noqa: F401

        print("✓ models")
    except Exception as e:
        errors.append(f"models: {e}")
        print(f"✗ models: {e}")

    try:
        import services  # noqa: F401

        print("✓ services")
    except Exception as e:
        errors.append(f"services: {e}")
        print(f"✗ services: {e}")

    try:
        import utils  # noqa: F401

        print("✓ utils")
    except Exception as e:
        errors.append(f"utils: {e}")
        print(f"✗ utils: {e}")

    return errors


def validate_models() -> List[str]:
    """Validate Pydantic models."""
    errors = []

    print("\nValidating Pydantic models...")

    try:
        from models import Monitor, BoundingBox, AppConfig, DetectionResult, ButtonType

        # Test Monitor
        mon = Monitor(x=0, y=0, width=1920, height=1080)
        assert mon.aspect_ratio > 1.7
        print("✓ Monitor model")

        # Test BoundingBox
        bbox = BoundingBox(x1=0, y1=0, x2=100, y2=100)
        padded = bbox.pad(0.1)
        assert padded.width < bbox.width
        print("✓ BoundingBox model")

        # Test AppConfig
        config = AppConfig(vortex=True, verbose=False)
        assert config.min_matches == 8
        print("✓ AppConfig model")

        # Test DetectionResult
        result = DetectionResult(
            button_type=ButtonType.VORTEX, x=100, y=200, confidence=0.95, num_matches=15
        )
        assert result.confidence < 1.0
        print("✓ DetectionResult model")

    except Exception as e:
        errors.append(f"Model validation: {e}")
        print(f"✗ Model validation: {e}")

    return errors


def validate_assets() -> List[str]:
    """Validate asset files exist."""
    errors = []

    print("\nValidating assets...")

    from pathlib import Path

    required_assets = [
        "VortexDownloadButton.png",
        "WebsiteDownloadButton.png",
        "WabbajackDownloadButton.png",
        "ClickHereButton.png",
        "UnderstoodButton.png",
        "StagingButton.png",
    ]
    optional_assets = [
        "VortexDownloadButtonNew.png",
        "WebsiteDownloadButtonNew.png",
    ]

    assets_path = Path("assets")

    if not assets_path.exists():
        errors.append("assets/ directory not found")
        print("✗ assets/ directory not found")
        return errors

    for asset in required_assets:
        asset_path = assets_path / asset
        if asset_path.exists():
            print(f"✓ {asset}")
        else:
            errors.append(f"Missing asset: {asset}")
            print(f"✗ Missing asset: {asset}")

    for asset in optional_assets:
        asset_path = assets_path / asset
        if asset_path.exists():
            print(f"✓ {asset} (optional)")
        else:
            print(f"- Optional asset not found: {asset}")

    return errors


def main() -> int:
    """Run all validations."""
    print("=" * 60)
    print("NexusAutoDL Validation Script")
    print("=" * 60)

    all_errors = []

    # Run validations
    all_errors.extend(validate_imports())
    all_errors.extend(validate_models())
    all_errors.extend(validate_assets())

    # Summary
    print("\n" + "=" * 60)
    if all_errors:
        print(f"❌ Validation FAILED with {len(all_errors)} error(s):")
        for error in all_errors:
            print(f"  - {error}")
        return 1
    else:
        print("✅ All validations PASSED!")
        print("\nYou can now run:")
        print("  python main.py --help")
        return 0


if __name__ == "__main__":
    sys.exit(main())
