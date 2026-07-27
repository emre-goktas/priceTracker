from __future__ import annotations

import os
from io import BytesIO

from dotenv import load_dotenv
from minio import Minio

from shared.logging_config import get_logger

load_dotenv()
logger = get_logger(__name__)


class StorageClient:
    """Ortak MinIO istemcisi.

    crawler/ ve core/ birbirini import edemediği (loosely coupled) için MinIO
    erişimi shared/ altında tutulur; her iki modül de bu istemciyi kullanır.
    """

    def __init__(self) -> None:
        raw_url = os.environ["MINIO_URL"]
        self._secure = raw_url.startswith("https://")
        endpoint = raw_url.replace("https://", "").replace("http://", "")

        self._client = Minio(
            endpoint,
            access_key=os.environ["MINIO_ROOT_USER"],
            secret_key=os.environ["MINIO_ROOT_PASSWORD"],
            secure=self._secure,
        )

    def ensure_bucket_exists(self, bucket: str) -> None:
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)
            logger.info(f"Bucket oluşturuldu: {bucket}")

    def put_bytes(
        self,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.ensure_bucket_exists(bucket)
        self._client.put_object(
            bucket, object_name, BytesIO(data), length=len(data), content_type=content_type
        )


storage = StorageClient()
