"""Utilities for managing Kubernetes secrets."""

from __future__ import annotations

import base64
from typing import Any

from kubernetes import client


def get_secret_value(
    api: client.CoreV1Api,
    namespace: str,
    secret_name: str,
    key: str,
) -> str:
    """Get a value from a Kubernetes secret.

    Args:
        api: Kubernetes API client
        namespace: Namespace of the secret
        secret_name: Name of the secret
        key: Key in the secret

    Returns:
        Secret value

    Raises:
        ValueError: If secret or key not found
    """
    try:
        secret = api.read_namespaced_secret(name=secret_name, namespace=namespace)
        if key not in secret.data:
            raise ValueError(f"Key '{key}' not found in secret '{secret_name}'")
        
        value = secret.data[key]
        # Handle both string and bytes (different versions of kubernetes client)
        if isinstance(value, str):
            # Try to base64 decode first (normal case)
            try:
                return base64.b64decode(value).decode("utf-8")
            except Exception:
                # If base64 decode fails, assume it's already decoded
                return value
        else:
            # Value is already bytes, decode it
            return value.decode("utf-8")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            raise ValueError(f"Secret '{secret_name}' not found in namespace '{namespace}'") from e
        raise


def create_secret(
    api: client.CoreV1Api,
    namespace: str,
    secret_name: str,
    data: dict[str, str],
    owner_references: list[dict[str, Any]] | None = None,
) -> None:
    """Create a Kubernetes secret.

    Args:
        api: Kubernetes API client
        namespace: Namespace for the secret
        secret_name: Name of the secret
        data: Secret data (will be base64 encoded)
        owner_references: Owner references for the secret
    """
    from ..constants import FIELD_MANAGER

    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=secret_name,
            namespace=namespace,
            owner_references=owner_references or [],
        ),
        type="Opaque",
        data={k: base64.b64encode(v.encode("utf-8")).decode("utf-8") for k, v in data.items()},
    )

    api.create_namespaced_secret(
        namespace=namespace,
        body=secret,
        field_manager=FIELD_MANAGER,
    )


def update_secret(
    api: client.CoreV1Api,
    namespace: str,
    secret_name: str,
    data: dict[str, str],
) -> None:
    """Update a Kubernetes secret.

    Args:
        api: Kubernetes API client
        namespace: Namespace of the secret
        secret_name: Name of the secret
        data: Secret data (will be base64 encoded)
    """
    from ..constants import FIELD_MANAGER

    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=secret_name, namespace=namespace),
        type="Opaque",
        data={k: base64.b64encode(v.encode("utf-8")).decode("utf-8") for k, v in data.items()},
    )

    api.patch_namespaced_secret(
        name=secret_name,
        namespace=namespace,
        body=secret,
        field_manager=FIELD_MANAGER,
    )


def delete_secret(
    api: client.CoreV1Api,
    namespace: str,
    secret_name: str,
) -> None:
    """Delete a Kubernetes secret.

    Args:
        api: Kubernetes API client
        namespace: Namespace of the secret
        secret_name: Name of the secret
    """
    api.delete_namespaced_secret(name=secret_name, namespace=namespace)

