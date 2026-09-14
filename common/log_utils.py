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

import os
import os.path
import logging
from logging.handlers import RotatingFileHandler
from common.file_utils import get_project_base_directory

initialized_root_logger = False

def init_root_logger(logfile_basename: str, log_format: str = None):
    """初始化 root logger。

    P2-4: 统一使用 JSON 结构化日志格式(复用 structured_logger.JSONFormatter)。
    OTel LoggingInstrumentor 会自动注入 trace_id / span_id 到 LogRecord。
    log_format 参数保留向后兼容,但不再使用(统一 JSON)。

    Args:
        logfile_basename: 日志文件基名(不含扩展名)
        log_format: 已废弃,保留向后兼容
    """
    global initialized_root_logger
    if initialized_root_logger:
        return
    initialized_root_logger = True

    logger = logging.getLogger()
    logger.handlers.clear()
    log_path = os.path.abspath(os.path.join(get_project_base_directory(), "logs", f"{logfile_basename}.log"))

    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # P2-4: 统一使用 JSON 格式,fail-safe 回退到传统格式
    try:
        from api.utils.structured_logger import JSONFormatter
        formatter = JSONFormatter()
    except ImportError:
        # structured_logger 不可用时回退到传统格式
        formatter = logging.Formatter(
            log_format or "%(asctime)-15s %(levelname)-8s %(process)d %(message)s"
        )

    handler1 = RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=5)
    handler1.setFormatter(formatter)
    logger.addHandler(handler1)

    handler2 = logging.StreamHandler()
    handler2.setFormatter(formatter)
    logger.addHandler(handler2)

    logging.captureWarnings(True)

    LOG_LEVELS = os.environ.get("LOG_LEVELS", "")
    pkg_levels = {}
    for pkg_name_level in LOG_LEVELS.split(","):
        terms = pkg_name_level.split("=")
        if len(terms)!= 2:
            continue
        pkg_name, pkg_level = terms[0], terms[1]
        pkg_name = pkg_name.strip()
        pkg_level = logging.getLevelName(pkg_level.strip().upper())
        if not isinstance(pkg_level, int):
            pkg_level = logging.INFO
        pkg_levels[pkg_name] = logging.getLevelName(pkg_level)

    for pkg_name in ['peewee', 'pdfminer']:
        if pkg_name not in pkg_levels:
            pkg_levels[pkg_name] = logging.getLevelName(logging.WARNING)
    if 'root' not in pkg_levels:
        pkg_levels['root'] = logging.getLevelName(logging.INFO)

    for pkg_name, pkg_level in pkg_levels.items():
        pkg_logger = logging.getLogger(pkg_name)
        pkg_logger.setLevel(pkg_level)

    msg = f"{logfile_basename} log path: {log_path}, log levels: {pkg_levels}"
    logger.info(msg)


def log_exception(e, *args):
    logging.exception(e)
    for a in args:
        try:
            text = getattr(a, "text")
        except Exception:
            text = None
        if text is not None:
            logging.error(text)
            raise Exception(text)
        logging.error(str(a))
    raise e
