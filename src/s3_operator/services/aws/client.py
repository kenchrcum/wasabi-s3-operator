"""AWS S3 client implementation."""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ..s3.base import S3Provider
from .models import BucketConfig

logger = logging.getLogger(__name__)


class AWSProvider:
    """AWS S3 provider implementation."""

    def __init__(
        self,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        session_token: str | None = None,
        path_style: bool = True,
        insecure_skip_verify: bool = False,
    ) -> None:
        """Initialize AWS S3 provider.

        Args:
            endpoint: S3 endpoint URL
            region: AWS region
            access_key: Access key ID
            secret_key: Secret access key
            session_token: Optional session token for temporary credentials
            path_style: Use path-style addressing
            insecure_skip_verify: Skip TLS verification
        """
        self.endpoint = endpoint
        self.region = region
        self.path_style = path_style

        # Configure boto3 client
        config = boto3.session.Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if path_style else "auto"},
        )

        # Configure SSL if needed
        if insecure_skip_verify:
            import ssl

            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
            config=config,
            verify=not insecure_skip_verify,
        )

    def list_buckets(self) -> list[str]:
        """List all buckets."""
        try:
            response = self.client.list_buckets()
            return [bucket["Name"] for bucket in response.get("Buckets", [])]
        except ClientError as e:
            logger.error(f"Failed to list buckets: {e}")
            raise

    def create_bucket(self, name: str, config: dict[str, Any]) -> None:
        """Create a bucket with configuration."""
        try:
            # Create bucket
            create_params = {"Bucket": name}
            region = config.get("region")
            if region:
                create_params["CreateBucketConfiguration"] = {"LocationConstraint": region}

            self.client.create_bucket(**create_params)

            # Configure versioning
            if config.get("versioning_enabled"):
                self.set_bucket_versioning(name, True, config.get("mfa_delete", False))

            # Configure encryption
            if config.get("encryption_enabled"):
                algorithm = config.get("encryption_algorithm", "AES256")
                kms_key_id = config.get("kms_key_id")
                self.set_bucket_encryption(name, algorithm, kms_key_id)

            # Set tags
            tags = config.get("tags")
            if tags:
                self.set_bucket_tags(name, tags)

        except ClientError as e:
            logger.error(f"Failed to create bucket {name}: {e}")
            raise

    def delete_bucket(self, name: str) -> None:
        """Delete a bucket."""
        try:
            self.client.delete_bucket(Bucket=name)
        except ClientError as e:
            logger.error(f"Failed to delete bucket {name}: {e}")
            raise

    def bucket_exists(self, name: str) -> bool:
        """Check if bucket exists."""
        try:
            self.client.head_bucket(Bucket=name)
            return True
        except ClientError:
            return False

    def get_bucket_versioning(self, name: str) -> dict[str, bool]:
        """Get bucket versioning configuration."""
        try:
            response = self.client.get_bucket_versioning(Bucket=name)
            return {
                "enabled": response.get("Status") == "Enabled",
                "mfa_delete": response.get("MFADelete") == "Enabled",
            }
        except ClientError as e:
            logger.error(f"Failed to get versioning for bucket {name}: {e}")
            raise

    def set_bucket_versioning(self, name: str, enabled: bool, mfa_delete: bool = False) -> None:
        """Set bucket versioning configuration."""
        try:
            versioning_config = {
                "Status": "Enabled" if enabled else "Suspended",
            }
            if mfa_delete:
                versioning_config["MFADelete"] = "Enabled"
            else:
                versioning_config["MFADelete"] = "Disabled"

            self.client.put_bucket_versioning(
                Bucket=name,
                VersioningConfiguration=versioning_config,
            )
        except ClientError as e:
            logger.error(f"Failed to set versioning for bucket {name}: {e}")
            raise

    def get_bucket_encryption(self, name: str) -> dict[str, str | None]:
        """Get bucket encryption configuration."""
        try:
            response = self.client.get_bucket_encryption(Bucket=name)
            rules = response.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
            if rules:
                sse_config = rules[0].get("ApplyServerSideEncryptionByDefault", {})
                return {
                    "algorithm": sse_config.get("SSEAlgorithm"),
                    "kms_key_id": sse_config.get("KMSMasterKeyID"),
                }
            return {"algorithm": None, "kms_key_id": None}
        except ClientError as e:
            # Encryption not configured
            if e.response["Error"]["Code"] == "ServerSideEncryptionConfigurationNotFoundError":
                return {"algorithm": None, "kms_key_id": None}
            logger.error(f"Failed to get encryption for bucket {name}: {e}")
            raise

    def set_bucket_encryption(self, name: str, algorithm: str, kms_key_id: str | None = None) -> None:
        """Set bucket encryption configuration."""
        try:
            encryption_config = {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": algorithm,
                        }
                    }
                ]
            }

            if kms_key_id:
                encryption_config["Rules"][0]["ApplyServerSideEncryptionByDefault"]["KMSMasterKeyID"] = (
                    kms_key_id
                )

            self.client.put_bucket_encryption(
                Bucket=name,
                ServerSideEncryptionConfiguration=encryption_config,
            )
        except ClientError as e:
            logger.error(f"Failed to set encryption for bucket {name}: {e}")
            raise

    def set_bucket_policy(self, name: str, policy: dict[str, Any]) -> None:
        """Set bucket policy."""
        try:
            import json

            self.client.put_bucket_policy(
                Bucket=name,
                Policy=json.dumps(policy),
            )
        except ClientError as e:
            logger.error(f"Failed to set policy for bucket {name}: {e}")
            raise

    def get_bucket_policy(self, name: str) -> dict[str, Any]:
        """Get bucket policy."""
        try:
            response = self.client.get_bucket_policy(Bucket=name)
            import json

            return json.loads(response["Policy"])
        except ClientError as e:
            logger.error(f"Failed to get policy for bucket {name}: {e}")
            raise

    def delete_bucket_policy(self, name: str) -> None:
        """Delete bucket policy."""
        try:
            self.client.delete_bucket_policy(Bucket=name)
        except ClientError as e:
            logger.error(f"Failed to delete policy for bucket {name}: {e}")
            raise

    def set_bucket_tags(self, name: str, tags: dict[str, str]) -> None:
        """Set bucket tags."""
        try:
            tag_set = [{"Key": k, "Value": v} for k, v in tags.items()]
            self.client.put_bucket_tagging(
                Bucket=name,
                Tagging={"TagSet": tag_set},
            )
        except ClientError as e:
            logger.error(f"Failed to set tags for bucket {name}: {e}")
            raise

    def test_connectivity(self) -> bool:
        """Test connectivity to the provider."""
        try:
            self.client.list_buckets()
            return True
        except Exception as e:
            logger.error(f"Connectivity test failed: {e}")
            return False

