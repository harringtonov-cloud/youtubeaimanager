"""Artifact storage: local disk (dev) or any S3-compatible bucket (production).

Workers render into a temp dir, upload the finished artifact, and only keep the *key* in the DB.
That makes every stage restart-safe even on hosts with ephemeral disks (Railway, etc.).
"""
from __future__ import annotations

import mimetypes
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .config import get_config


class Storage:
    def put_file(self, local_path: str | Path, key: str, content_type: Optional[str] = None) -> None: ...
    def get_file(self, key: str, dest: str | Path) -> Path: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def url_for(self, key: str, expires: int = 3600) -> str: ...
    @property
    def kind(self) -> str: ...


class LocalStorage(Storage):
    kind = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve() / "media"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError("invalid storage key")
        return p

    def put_file(self, local_path, key, content_type=None):
        dest = self.path_for(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(local_path).resolve() != dest:
            shutil.copyfile(local_path, dest)

    def get_file(self, key, dest):
        src = self.path_for(key)
        if not src.exists():
            raise FileNotFoundError(key)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    def exists(self, key):
        try:
            return self.path_for(key).exists()
        except ValueError:
            return False

    def delete(self, key):
        try:
            self.path_for(key).unlink(missing_ok=True)
        except ValueError:
            pass

    def url_for(self, key, expires=3600):
        # served by the authenticated /api/media endpoint (through the dashboard proxy)
        return f"/api/media/{key}"


class S3Storage(Storage):
    kind = "s3"

    def __init__(self):
        import boto3
        from botocore.config import Config as BotoConfig

        c = get_config()
        if not (c.s3_bucket and c.s3_access_key_id and c.s3_secret_access_key):
            raise RuntimeError("S3 storage selected but S3_BUCKET / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY missing")
        self.bucket = c.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=c.s3_endpoint_url or None,
            aws_access_key_id=c.s3_access_key_id,
            aws_secret_access_key=c.s3_secret_access_key,
            region_name=c.s3_region or "auto",
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )

    def put_file(self, local_path, key, content_type=None):
        ctype = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        self.client.upload_file(str(local_path), self.bucket, key, ExtraArgs={"ContentType": ctype})

    def get_file(self, key, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(dest))
        return dest

    def exists(self, key):
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def url_for(self, key, expires=3600):
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
        )


@lru_cache
def get_storage() -> Storage:
    c = get_config()
    if c.storage_backend.lower() == "s3":
        return S3Storage()
    return LocalStorage(c.data_dir)
