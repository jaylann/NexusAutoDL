"""Button type enumeration."""

from enum import Enum


class ButtonType(str, Enum):
    """Types of buttons to detect."""

    VORTEX = "vortex"
    WEBSITE = "website"
    WABBAJACK = "wabbajack"
    CLICK = "click"
    UNDERSTOOD = "understood"
    STAGING = "staging"
    # "Standard download" choice in Nexus's beta resumable-download modal
    # (offered for files >500MB). Wabbajack can only intercept the standard
    # browser download, so the clicker takes this option to dismiss the modal.
    STANDARD_DOWNLOAD = "standard_download"
