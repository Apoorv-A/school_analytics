"""Connector fetch and configuration errors."""


class ConnectorError(Exception):
    """Base class for connector failures."""


class ConnectorConfigError(ConnectorError):
    """Invalid or incomplete connector configuration."""


class ConnectorFetchError(ConnectorError):
    """Bundle could not be retrieved from the upstream ERP export."""


class ConnectorNotImplementedError(ConnectorError):
    """Connector kind is reserved but not yet available in this release."""
