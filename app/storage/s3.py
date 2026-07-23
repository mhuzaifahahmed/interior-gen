import boto3

from app.config import settings
from app.storage.base import Storage


class S3Storage(Storage):
    """S3-compatible storage. Works unmodified against Cloudflare R2 (recommended,
    free 10GB + no egress fees) or AWS S3 - only the endpoint/credentials differ.
    """

    def __init__(self) -> None:
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        )

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def url(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=3600,
        )
