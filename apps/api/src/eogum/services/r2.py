import uuid

import boto3
from botocore.config import Config

from eogum.config import settings

_client = None


def get_r2_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    return _client


def generate_presigned_upload(filename: str, content_type: str) -> tuple[str, str]:
    """Generate presigned PUT URL for direct upload from frontend.

    Returns (upload_url, r2_key).
    """
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    r2_key = f"sources/{uuid.uuid4()}.{ext}" if ext else f"sources/{uuid.uuid4()}"

    client = get_r2_client()
    upload_url = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": r2_key,
            "ContentType": content_type,
        },
        ExpiresIn=3600,
    )
    return upload_url, r2_key


def generate_presigned_download(r2_key: str, filename: str) -> str:
    """Generate presigned GET URL for file download."""
    client = get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": r2_key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=3600,
    )


def generate_presigned_stream(r2_key: str) -> str:
    """Presigned GET for inline streaming (no Content-Disposition: attachment)."""
    client = get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket_name, "Key": r2_key},
        ExpiresIn=3600,
    )


def create_multipart_upload(r2_key: str, content_type: str) -> str:
    """Initiate multipart upload, return upload_id."""
    client = get_r2_client()
    resp = client.create_multipart_upload(
        Bucket=settings.r2_bucket_name,
        Key=r2_key,
        ContentType=content_type,
    )
    return resp["UploadId"]


def generate_presigned_upload_part(r2_key: str, upload_id: str, part_number: int) -> str:
    """Generate presigned URL for uploading a single part."""
    client = get_r2_client()
    return client.generate_presigned_url(
        "upload_part",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": r2_key,
            "UploadId": upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=3600,
    )


def complete_multipart_upload(r2_key: str, upload_id: str, parts: list[dict]) -> None:
    """Complete multipart upload. parts = [{"PartNumber": int, "ETag": str}, ...]"""
    client = get_r2_client()
    client.complete_multipart_upload(
        Bucket=settings.r2_bucket_name,
        Key=r2_key,
        UploadId=upload_id,
        MultipartUpload={"Parts": parts},
    )


def abort_multipart_upload(r2_key: str, upload_id: str) -> None:
    """Abort a multipart upload."""
    client = get_r2_client()
    client.abort_multipart_upload(
        Bucket=settings.r2_bucket_name,
        Key=r2_key,
        UploadId=upload_id,
    )


def download_to_bytes(r2_key: str) -> bytes:
    """Download file from R2 and return as bytes."""
    client = get_r2_client()
    response = client.get_object(Bucket=settings.r2_bucket_name, Key=r2_key)
    return response["Body"].read()


def download_file(r2_key: str, local_path: str) -> str:
    """Download file from R2 to local path."""
    client = get_r2_client()
    client.download_file(settings.r2_bucket_name, r2_key, local_path)
    return local_path


def upload_file(local_path: str, r2_key: str, content_type: str = "application/octet-stream") -> str:
    """Upload local file to R2."""
    client = get_r2_client()
    client.upload_file(local_path, settings.r2_bucket_name, r2_key, ExtraArgs={"ContentType": content_type})
    return r2_key
