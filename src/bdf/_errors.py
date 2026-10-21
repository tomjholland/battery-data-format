"""Custom error classes."""


class BDFValidationError(Exception):
    """Raised when a DataFrame fails BDF validation."""


class BDFMetadataError(Exception):
    """Raised when a metadata sidecar exists and cannot be restored."""


class BDFColumnError(ValueError):
    """Raised when a requested BDF column cannot be parsed or resolved from the data."""
