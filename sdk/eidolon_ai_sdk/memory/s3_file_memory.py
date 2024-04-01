import fnmatch
from io import BytesIO

import boto3
from botocore.exceptions import ClientError
from mypy_boto3_s3 import S3ServiceResource
from pydantic import BaseModel

from eidolon_ai_sdk.memory.file_memory import FileMemory
from eidolon_ai_sdk.util.async_wrapper import make_async


class S3FileMemory(BaseModel, FileMemory):
    bucket: str
    region: str = "us-east-1"
    create_bucket_on_startup: bool = False
    _s3: S3ServiceResource

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._s3 = boto3.resource('s3', self.region)

    @make_async
    def start(self):
        if self.create_bucket_on_startup:
            self._s3.Bucket(self.bucket).create()

    async def stop(self):
        pass

    @make_async
    def read_file(self, file_path: str) -> bytes:
        with BytesIO() as buffer:
            self._s3.Bucket(self.bucket).download_fileobj(file_path, buffer)
            buffer.seek(0)
            return buffer.read()

    @make_async
    def write_file(self, file_path: str, file_contents: bytes) -> None:
        with BytesIO() as buffer:
            buffer.write(file_contents)
            buffer.seek(0)
            self._s3.Bucket(self.bucket).upload_fileobj(buffer, file_path)

    @make_async
    def delete_file(self, file_path: str) -> None:
        self._s3.Bucket(self.bucket).delete_objects(Delete={'Objects': [{'Key': file_path}]})

    async def mkdir(self, directory: str, exist_ok: bool = False):
        pass

    @make_async
    def exists(self, file_name: str):
        for page in self._s3.Bucket(self.bucket).objects.filter(Prefix=file_name).pages():
            for object in page:
                if object.key == file_name:
                    return True
        return False

    @make_async
    def glob(self, pattern: str):
        acc = []
        for page in self._s3.Bucket(self.bucket).objects.pages():
            matching = fnmatch.filter((o.key for o in page), pattern)
            acc.extend(matching)
        return acc
