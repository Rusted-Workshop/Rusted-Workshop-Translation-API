"""
S3 文件服务
"""

import os
import zipfile
from typing import Optional

import aioboto3
import boto3
from botocore.exceptions import ClientError
from botocore.client import Config


class S3Service:
    """S3 文件操作服务（异步）"""

    def __init__(
        self,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ):
        self.session = aioboto3.Session(
            aws_access_key_id=aws_access_key_id or os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=aws_secret_access_key
            or os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=region_name,
        )
        self.endpoint_url = endpoint_url
        self.aws_access_key_id = aws_access_key_id or os.getenv("AWS_ACCESS_KEY_ID")
        self.aws_secret_access_key = aws_secret_access_key or os.getenv(
            "AWS_SECRET_ACCESS_KEY"
        )
        self.region_name = region_name

    async def download_file(self, s3_url: str, local_path: str) -> str:
        """
        从S3下载文件

        Args:
            s3_url: S3 URL (格式: s3://bucket/key 或 https://...)
            local_path: 本地保存路径

        Returns:
            本地文件路径
        """
        bucket, key = self._parse_s3_url(s3_url)

        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            async with self.session.client("s3", endpoint_url=self.endpoint_url) as s3:  # type: ignore
                await s3.download_file(bucket, key, local_path)
            return local_path
        except ClientError as e:
            raise Exception(f"Failed to download from S3: {e}")

    async def upload_file(self, local_path: str, bucket: str, key: str) -> str:
        """
        上传文件到S3

        Args:
            local_path: 本地文件路径
            bucket: S3存储桶名称
            key: S3对象键

        Returns:
            S3 URL
        """
        try:
            async with self.session.client("s3", endpoint_url=self.endpoint_url) as s3:  # type: ignore
                await s3.upload_file(local_path, bucket, key)
            return f"s3://{bucket}/{key}"
        except ClientError as e:
            raise Exception(f"Failed to upload to S3: {e}")

    async def upload_directory(self, local_dir: str, bucket: str, prefix: str) -> str:
        """
        上传整个目录到S3

        Args:
            local_dir: 本地目录路径
            bucket: S3存储桶名称
            prefix: S3对象键前缀

        Returns:
            S3 URL prefix
        """
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, local_dir)
                s3_key = os.path.join(prefix, relative_path).replace("\\", "/")
                await self.upload_file(local_path, bucket, s3_key)

        return f"s3://{bucket}/{prefix}"

    def generate_presigned_upload_url(
        self,
        bucket: str,
        key: str,
        expiration: int = 3600,
        content_type: Optional[str] = None,
    ) -> str:
        """
        生成预签名上传URL

        Args:
            bucket: S3存储桶名称
            key: S3对象键
            expiration: URL过期时间（秒），默认3600秒（1小时）
            content_type: 可选的内容类型，例如 'application/zip'

        Returns:
            预签名上传URL
        """
        try:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.region_name,
                endpoint_url=self.endpoint_url,
                config=Config(
                    signature_version="s3v4"
                ),
            )

            params = {
                "Bucket": bucket,
                "Key": key
            }

            if content_type:
                params["ContentType"] = content_type

            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expiration,
            )

            return presigned_url
        except ClientError as e:
            raise Exception(f"Failed to generate presigned URL: {e}")

    def generate_presigned_download_url(
        self,
        bucket: str,
        key: str,
        expiration: int = 3600,
    ) -> str:
        """
        生成预签名下载URL

        Args:
            bucket: S3存储桶名称
            key: S3对象键
            expiration: URL过期时间（秒），默认3600秒（1小时）

        Returns:
            预签名下载URL
        """
        try:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.region_name,
                endpoint_url=self.endpoint_url,
                config=Config(
                    signature_version="s3v4"
                ),
            )

            presigned_url = s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": key,
                },
                ExpiresIn=expiration,
            )

            return presigned_url
        except ClientError as e:
            raise Exception(f"Failed to generate presigned download URL: {e}")

    def _parse_s3_url(self, s3_url: str) -> tuple[str, str]:
        """
        解析S3 URL

        Args:
            s3_url: S3 URL

        Returns:
            (bucket, key) 元组
        """
        if s3_url.startswith("s3://"):
            parts = s3_url[5:].split("/", 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid S3 URL: {s3_url}")
            return parts[0], parts[1]
        elif s3_url.startswith("https://"):
            # 支持 https://bucket.s3.region.amazonaws.com/key 格式
            if ".s3." in s3_url or ".s3-" in s3_url:
                parts = s3_url.split("/", 3)
                bucket = parts[2].split(".")[0]
                key = parts[3] if len(parts) > 3 else ""
                return bucket, key
            else:
                raise ValueError(f"Invalid S3 HTTPS URL: {s3_url}")
        else:
            raise ValueError(f"Unsupported URL format: {s3_url}")


def extract_archive(archive_path: str, extract_to: str) -> str:
    """
    解压文件

    Args:
        archive_path: 压缩文件路径
        extract_to: 解压目标路径

    Returns:
        解压后的目录路径
    """
    os.makedirs(extract_to, exist_ok=True)

    if archive_path.endswith(".zip") or archive_path.endswith(".rwmod"):
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            zip_ref.extractall(extract_to)
    else:
        raise ValueError(f"Unsupported archive format: {archive_path}")

    return extract_to


def create_archive(source_dir: str, archive_path: str, format: str = "zip") -> str:
    """
    创建压缩文件

    Args:
        source_dir: 源目录路径
        archive_path: 压缩文件保存路径
        format: 压缩格式 (zip)

    Returns:
        压缩文件路径
    """
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)

    if format == "zip":
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zip_ref:
            for root, dirs, files in os.walk(source_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, source_dir)
                    zip_ref.write(file_path, arcname)
    else:
        raise ValueError(f"Unsupported format: {format}")

    return archive_path
