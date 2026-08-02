from __future__ import annotations

import json
import os
from collections.abc import Iterator
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

    Okuma tarafı (list_object_names/get_bytes) PUBLIC metot olarak burada yaşar — çağıranların
    içerideki minio istemcisine uzanması gerekmez. Bu sadece kapsülleme meselesi değil bir hata
    kaynağıydı: minio'nun get_object() bir urllib3 yanıtı döner ve close()+release_conn()
    çağrılmazsa bağlantı havuzu binlerce obje okunduğunda tükenir (get_bytes bunu yapar).
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

    def list_object_names(self, bucket: str, prefix: str) -> Iterator[str]:
        """Verilen prefix altındaki tüm objelerin adlarını (recursive) üretir."""
        for obj in self._client.list_objects(bucket, prefix=prefix, recursive=True):
            yield obj.object_name

    def get_bytes(self, bucket: str, object_name: str) -> bytes:
        """Bir objenin tamamını okur ve HTTP bağlantısını havuza geri verir."""
        response = self._client.get_object(bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

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

    def put_json_with_meta(
        self,
        bucket: str,
        object_name: str,
        content: bytes,
        meta: dict,
        content_type: str = "application/json",
    ) -> None:
        """Ham veriyi ve yanındaki .meta.json sidecar'ı birlikte yazar (crawler/ ve core/
        ortak arşivleme deseni: her obje + fetch_timestamp/http_status/content_hash meta'sı)."""
        self.put_bytes(bucket, object_name, content, content_type=content_type)
        self.put_bytes(
            bucket,
            f"{object_name}.meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )


# Tembel (lazy) singleton: modül import edilir edilmez StorageClient() kurulursa MINIO_URL
# ortam değişkeni OLMADAN bu modülü import eden her şey (örn. `crawler/cli.py --help`, unit
# testler, ruff dışı statik araçlar) KeyError ile patlar. İstemci ilk gerçek kullanımda kurulur.
_singleton: StorageClient | None = None


def get_storage() -> StorageClient:
    global _singleton
    if _singleton is None:
        _singleton = StorageClient()
    return _singleton


class _StorageProxy:
    """`from shared.storage import storage` çağrıları aynen çalışmaya devam etsin ama
    gerçek bağlantı ilk öznitelik erişimine kadar kurulmasın."""

    def __getattr__(self, name: str):
        return getattr(get_storage(), name)


storage = _StorageProxy()
