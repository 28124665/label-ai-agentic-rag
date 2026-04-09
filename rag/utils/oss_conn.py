#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import logging
import re
import boto3
from botocore.exceptions import ClientError
from botocore.config import Config
import time
from io import BytesIO
from urllib.parse import urlparse
from common.decorator import singleton
from common import settings

# Collapse duplicate slashes in S3/MinIO object keys (MinIO rejects keys like a//b).
_S3_KEY_SLASHES = re.compile(r"/+")


@singleton
class RAGFlowOSS:
    def __init__(self):
        self.conn = None
        self.oss_config = settings.OSS
        self.access_key = self.oss_config.get('access_key', None)
        self.secret_key = self.oss_config.get('secret_key', None)
        self.endpoint_url = self.oss_config.get('endpoint_url', None)
        self.region = self.oss_config.get('region', None)
        self.bucket = self.oss_config.get('bucket', None)
        self.prefix_path = self.oss_config.get('prefix_path', None)
        self.signature_version = self.oss_config.get('signature_version', None)
        self.addressing_style = self.oss_config.get('addressing_style', None)
        self.__open__()

    @staticmethod
    def _normalize_object_key(prefix_path, fnm):
        """
        Build a single-slash object key for S3-compatible stores (OSS, MinIO).
        Strips leading/trailing slashes on segments and removes accidental '//'.
        """
        parts = []
        if prefix_path and str(prefix_path).strip():
            p = str(prefix_path).replace("\\", "/").strip("/")
            if p:
                parts.append(p)
        if fnm is not None and str(fnm).strip():
            n = str(fnm).replace("\\", "/").lstrip("/")
            if n:
                parts.append(n)
        if not parts:
            return ""
        joined = "/".join(parts)
        return _S3_KEY_SLASHES.sub("/", joined).strip("/")

    def _parse_oss_url(self, url):
        """
        解析 OSS URL，提取 bucket 和 key
        支持格式：
        1. http://bucket.oss-region.aliyuncs.com/path
        2. https://bucket.oss-region.aliyuncs.com/path
        3. oss://bucket/path
        4. minio://bucket/path（与 oss:// 语义相同，用于 S3 兼容/MinIO）
        """
        parsed = urlparse(url)

        if parsed.scheme in ("oss", "minio"):
            # 处理 oss://bucket/path、minio://bucket/path
            bucket = parsed.netloc
            key = parsed.path.lstrip("/")
        elif parsed.scheme in ['http', 'https']:
            # 原有逻辑：http://bucket.oss-region.aliyuncs.com/path
            hostname_parts = parsed.netloc.split('.')
            if len(hostname_parts) < 4:
                return None, None
            bucket = hostname_parts[0]
            key = parsed.path.lstrip('/')
        else:
            return None, None

        # 去掉前缀 self.prefix_path
        # if self.prefix_path and key.startswith(self.prefix_path):
        #     key = key[len(self.prefix_path):]

        # 去掉开头的 /
        key = key.lstrip('/')

        return bucket, key

        # 从域名中提取 bucket
        # 格式: bucket.oss-region.aliyuncs.com
        hostname_parts = parsed.netloc.split('.')
        if len(hostname_parts) < 4:
            return None, None

        bucket = hostname_parts[0]
        key = parsed.path.lstrip('/')

        return bucket, key

    @staticmethod
    def use_default_bucket(method):
        def wrapper(self, bucket, *args, **kwargs):
            # If there is a default bucket, use the default bucket
            actual_bucket = self.bucket if self.bucket else bucket
            return method(self, actual_bucket, *args, **kwargs)

        return wrapper

    @staticmethod
    def use_prefix_path(method):
        def wrapper(self, bucket, fnm, *args, **kwargs):
            # 检查 fnm 是否为完整 URL
            if fnm.startswith(("http://", "https://", "oss://", "minio://")):
                parsed_bucket, parsed_key = self._parse_oss_url(fnm)
                if parsed_bucket and parsed_key:
                    # 使用解析出的 bucket 和 key
                    parsed_key = _S3_KEY_SLASHES.sub("/", parsed_key).strip("/")
                    return method(self, parsed_bucket, parsed_key, *args, **kwargs)

            # 检查 fnm 是否以 boss_ai/knowledge_data/ 开头
            if not fnm.startswith('boss_ai/collector_data/') and self.prefix_path:
                fnm = self._normalize_object_key(self.prefix_path, fnm)
            else:
                fnm = _S3_KEY_SLASHES.sub("/", str(fnm).replace("\\", "/").lstrip("/")).strip("/")
            return method(self, bucket, fnm, *args, **kwargs)

        return wrapper

    def __open__(self):
        try:
            if self.conn:
                self.__close__()
        except Exception:
            pass

        try:
            config_kwargs = {}

            if self.signature_version:
                config_kwargs['signature_version'] = self.signature_version
            if self.addressing_style:
                config_kwargs['s3'] = {
                    'addressing_style': self.addressing_style
                }

            config = Config(**config_kwargs) if config_kwargs else None

            # Reference：https://help.aliyun.com/zh/oss/developer-reference/use-amazon-s3-sdks-to-access-oss
            self.conn = boto3.client(
                's3',
                region_name=self.region,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                endpoint_url=self.endpoint_url,
                config=config
            )
        except Exception:
            logging.exception(f"Fail to connect at region {self.region}")

    def __close__(self):
        del self.conn
        self.conn = None

    @use_default_bucket
    def bucket_exists(self, bucket):
        try:
            logging.debug(f"head_bucket bucketname {bucket}")
            self.conn.head_bucket(Bucket=bucket)
            exists = True
        except ClientError:
            logging.exception(f"head_bucket error {bucket}")
            exists = False
        return exists

    def health(self):
        bucket = self.bucket
        fnm = "txtxtxtxt1"
        if self.prefix_path:
            fnm = self._normalize_object_key(self.prefix_path, fnm)
        fnm, binary = fnm, b"_t@@@1"
        if not self.bucket_exists(bucket):
            self.conn.create_bucket(Bucket=bucket)
            logging.debug(f"create bucket {bucket} ********")

        r = self.conn.upload_fileobj(BytesIO(binary), bucket, fnm)
        return r

    def get_properties(self, bucket, key):
        return {}

    def list(self, bucket, dir, recursive=True):
        return []

    @use_prefix_path
    @use_default_bucket
    def put(self, bucket, fnm, binary, tenant_id=None):
        logging.debug(f"bucket name {bucket}; filename :{fnm}:")
        for _ in range(1):
            try:
                if not self.bucket_exists(bucket):
                    self.conn.create_bucket(Bucket=bucket)
                    logging.info(f"create bucket {bucket} ********")
                r = self.conn.upload_fileobj(BytesIO(binary), bucket, fnm)

                return r
            except Exception:
                logging.exception(f"Fail put {bucket}/{fnm}")
                self.__open__()
                time.sleep(1)

    @use_prefix_path
    @use_default_bucket
    def rm(self, bucket, fnm, tenant_id=None):
        try:
            self.conn.delete_object(Bucket=bucket, Key=fnm)
        except Exception:
            logging.exception(f"Fail rm {bucket}/{fnm}")

    @use_prefix_path
    @use_default_bucket
    def get(self, bucket, fnm, tenant_id=None):
        logging.info(f"bucket name {bucket}; filename :{fnm}:")

        for _ in range(1):
            try:
                r = self.conn.get_object(Bucket=bucket, Key=fnm)
                object_data = r['Body'].read()
                return object_data
            except Exception:
                logging.exception(f"fail get {bucket}/{fnm}")
                self.__open__()
                time.sleep(1)
        return None

    @use_default_bucket
    def get_no_prefix_path(self, bucket, fnm, tenant_id=None):
        for _ in range(1):
            try:
                r = self.conn.get_object(Bucket=bucket, Key=fnm)
                object_data = r['Body'].read()
                return object_data
            except Exception:
                logging.exception(f"fail get {bucket}/{fnm}")
                self.__open__()
                time.sleep(1)
        return None

    @use_prefix_path
    @use_default_bucket
    def obj_exist(self, bucket, fnm, tenant_id=None):
        try:
            if self.conn.head_object(Bucket=bucket, Key=fnm):
                return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            else:
                raise

    @use_prefix_path
    @use_default_bucket
    def get_presigned_url(self, bucket, fnm, expires, tenant_id=None):
        for _ in range(10):
            try:
                r = self.conn.generate_presigned_url('get_object',
                                                     Params={'Bucket': bucket,
                                                             'Key': fnm},
                                                     ExpiresIn=expires)

                return r
            except Exception:
                logging.exception(f"fail get url {bucket}/{fnm}")
                self.__open__()
                time.sleep(1)
        return None