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
import os
import threading
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from peewee import CharField

from agent.component.state_fields import (
    STATE_CONFIG_DEFAULTS,
    canvas_state_key,
)
from api.db.db_models import DB, DataBaseModel, JSONField
from api.db.services.common_service import CommonService
from api.utils.state_crypto import (
    secure_deserialize_persistent,
    secure_serialize_persistent,
    secure_serialize_runtime,
)
from common.misc_utils import get_uuid
from rag.utils.redis_conn import REDIS_CONN

"""检查点时间旅行服务层。

对应需求：P2-FR-05（检查点时间旅行）

功能说明：
  提供检查点的查询、加载、Replay fork、保留策略清理等核心服务。

实现方式：
  1. 检查点存储格式：
     - Redis key: {run_id}-checkpoint:{node_name}:{timestamp_ms}
     - value: MessagePack + gzip + AES 加密的状态快照
     - 运行时状态: {run_id}-state（JSON 格式，带 TTL）
  2. CheckpointService：
     - list_checkpoints：扫描 Redis 中的检查点 key，解析节点名和时间戳
     - load_checkpoint：反序列化检查点状态
     - fork_run：从检查点深拷贝状态，生成新 run_id，写入新状态和检查点
       - 只允许覆盖白名单字段（query、rewritten_query）
       - 记录 Replay 依赖关系（用于清理时保护活跃的子 run）
       - 记录审计日志到数据库
     - cleanup_expired：按保留策略清理过期检查点
       - 保留最新 N 个（keep_latest）
       - 删除超过 TTL 的
       - 限制每个 run 最多 max_per_run 个
       - 跳过有活跃 Replay 依赖的 run
  3. CheckpointReplayAuditLog：审计日志数据库模型
     记录每次 Replay 的 user_id、run_id、checkpoint_id、new_run_id、override_state
  4. CheckpointCleanupTask：后台清理调度器
     以 daemon 线程运行，默认每小时执行一次清理
  5. 所有配置可通过环境变量覆盖：
     CHECKPOINT_ENABLED_NODES、CHECKPOINT_MAX_PER_RUN、CHECKPOINT_TTL_DAYS 等
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# 检查点配置（均可通过环境变量覆盖）
# CHECKPOINT_ENABLED_NODES: 启用检查点的节点列表
# CHECKPOINT_MAX_PER_RUN: 每个 run 最多保留的检查点数
# CHECKPOINT_TTL_DAYS: 检查点保留天数
# CHECKPOINT_KEEP_LATEST: 无论如何保留的最新检查点数
# CHECKPOINT_DEBUG_MODE_EXTENSION_DAYS: 调试模式下延长的保留天数
# CHECKPOINT_CLEANUP_INTERVAL_HOURS: 清理调度间隔（小时）

CHECKPOINT_ENABLED_NODES: Tuple[str, ...] = tuple(
    os.environ.get(
        "CHECKPOINT_ENABLED_NODES",
        ",".join(["begin", "categorize", "retrieval", "generator"]),
    ).split(",")
)
CHECKPOINT_MAX_PER_RUN = int(os.environ.get("CHECKPOINT_MAX_PER_RUN", 20))
CHECKPOINT_TTL_DAYS = int(os.environ.get("CHECKPOINT_TTL_DAYS", 7))
CHECKPOINT_KEEP_LATEST = int(os.environ.get("CHECKPOINT_KEEP_LATEST", 5))
CHECKPOINT_DEBUG_MODE_EXTENSION_DAYS = int(
    os.environ.get("CHECKPOINT_DEBUG_MODE_EXTENSION_DAYS", 30)
)
CHECKPOINT_CLEANUP_INTERVAL_HOURS = int(
    os.environ.get("CHECKPOINT_CLEANUP_INTERVAL_HOURS", 1)
)
CHECKPOINT_DEBUG_MODE = (
    os.environ.get("CHECKPOINT_DEBUG_MODE", "false").lower() == "true"
)

# Replay 时允许覆盖的状态字段白名单
# 只有这些字段可以在 Replay 时被修改（如调整查询重新检索）
REPLAY_OVERRIDABLE_FIELDS: Set[str] = {
    "query",
    "rewritten_query",
}

# Replay 时禁止覆盖的保护字段
# 防止通过 Replay 篡改用户身份和租户信息
REPLAY_PROTECTED_FIELDS: Set[str] = {
    "user_id",
    "tenant_id",
    "sys.user_id",
}

_STATE_TTL_SECONDS = int(
    os.environ.get("STATE_TTL_SECONDS", STATE_CONFIG_DEFAULTS["state_ttl_seconds"])
)


def _checkpoint_key_prefix(run_id: str) -> str:
    return f"{run_id}-checkpoint"


def _runtime_state_key(run_id: str) -> str:
    return f"{run_id}-state"


def _dependents_key(run_id: str) -> str:
    return f"checkpoint:dependents:{run_id}"


def _build_checkpoint_key(run_id: str, node_name: str, timestamp_ms: int) -> str:
    return f"{_checkpoint_key_prefix(run_id)}:{node_name}:{timestamp_ms}"


def _parse_checkpoint_key(key: str) -> Tuple[str, str, int]:
    """Parse a checkpoint Redis key into (run_id, node_name, timestamp_ms)."""
    prefix, rest = key.split("-checkpoint:", 1)
    node_name, ts_str = rest.rsplit(":", 1)
    return prefix, node_name, int(ts_str)


# ---------------------------------------------------------------------------
# Audit log model
# ---------------------------------------------------------------------------

class CheckpointReplayAuditLog(DataBaseModel):
    """检查点 Replay 审计日志数据库模型。

    记录每次 Replay 操作的完整信息，用于安全审计和追溯。
    表名：checkpoint_replay_audit_log
    """
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    run_id = CharField(max_length=32, null=False, index=True)
    checkpoint_id = CharField(max_length=512, null=False, index=True)
    new_run_id = CharField(max_length=32, null=False, index=True)
    override_state = JSONField(null=True, default=dict)

    class Meta:
        db_table = "checkpoint_replay_audit_log"


_audit_table_ensured = False
_audit_table_lock = threading.Lock()


@DB.connection_context()
def _ensure_audit_table() -> None:
    global _audit_table_ensured
    with _audit_table_lock:
        if _audit_table_ensured:
            return
        try:
            if not CheckpointReplayAuditLog.table_exists():
                CheckpointReplayAuditLog.create_table(safe=True)
            _audit_table_ensured = True
        except Exception as e:
            logging.warning("Failed to ensure checkpoint audit log table: %s", e)


class CheckpointReplayAuditLogService(CommonService):
    model = CheckpointReplayAuditLog

    @classmethod
    def record(
        cls,
        user_id: str,
        run_id: str,
        checkpoint_id: str,
        new_run_id: str,
        override_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        _ensure_audit_table()
        try:
            cls.insert(
                user_id=user_id,
                run_id=run_id,
                checkpoint_id=checkpoint_id,
                new_run_id=new_run_id,
                override_state=override_state or {},
            )
        except Exception as e:
            logging.warning(
                "Failed to record checkpoint replay audit log: run=%s cp=%s new=%s error=%s",
                run_id,
                checkpoint_id,
                new_run_id,
                e,
            )


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

def _redis_client():
    return REDIS_CONN.REDIS


def _scan_checkpoint_keys(run_id: str) -> List[str]:
    client = _redis_client()
    if not client:
        return []
    pattern = f"{run_id}-checkpoint:*"
    try:
        return list(client.scan_iter(match=pattern, count=100))
    except Exception as e:
        logging.warning("Failed to scan checkpoint keys for run %s: %s", run_id, e)
        return []


def _load_checkpoint_payload(key: str) -> Optional[Dict[str, Any]]:
    client = _redis_client()
    if not client:
        return None
    try:
        payload = client.get(key)
        if not payload:
            return None
        return secure_deserialize_persistent(payload)
    except Exception as e:
        logging.warning("Failed to deserialize checkpoint %s: %s", key, e)
        return None


def _persist_runtime_state(run_id: str, state: Dict[str, Any]) -> bool:
    client = _redis_client()
    if not client:
        return False
    try:
        payload = secure_serialize_runtime(state)
        client.set(_runtime_state_key(run_id), payload, _STATE_TTL_SECONDS)
        return True
    except Exception as e:
        logging.warning("Failed to persist runtime state for run %s: %s", run_id, e)
        return False


def _persist_checkpoint(
    run_id: str,
    node_name: str,
    timestamp_ms: int,
    state: Dict[str, Any],
    ttl_seconds: int,
) -> Optional[str]:
    client = _redis_client()
    if not client:
        return None
    try:
        key = _build_checkpoint_key(run_id, node_name, timestamp_ms)
        payload = secure_serialize_persistent(state)
        client.set(key, payload, ttl_seconds)
        return key
    except Exception as e:
        logging.warning("Failed to persist checkpoint for run %s: %s", run_id, e)
        return None


def _record_replay_dependency(parent_run_id: str, child_run_id: str) -> None:
    client = _redis_client()
    if not client:
        return
    try:
        key = _dependents_key(parent_run_id)
        client.sadd(key, child_run_id)
        client.expire(key, _STATE_TTL_SECONDS)
    except Exception as e:
        logging.warning(
            "Failed to record replay dependency %s -> %s: %s",
            parent_run_id,
            child_run_id,
            e,
        )


def _has_active_replay_dependencies(run_id: str) -> bool:
    client = _redis_client()
    if not client:
        return False
    try:
        key = _dependents_key(run_id)
        child_ids = client.smembers(key)
        if not child_ids:
            return False
        for child_id in child_ids:
            if client.exists(_runtime_state_key(child_id)):
                return True
        # Clean up stale dependency set if no active children remain.
        client.delete(key)
        return False
    except Exception as e:
        logging.warning("Failed to check replay dependencies for %s: %s", run_id, e)
        return False


# ---------------------------------------------------------------------------
# Checkpoint service
# ---------------------------------------------------------------------------

class CheckpointService:
    """检查点时间旅行核心服务。

    提供检查点的 CRUD 操作和保留策略管理。
    所有数据存储在 Redis 中，审计日志存储在数据库中。
    """

    @staticmethod
    def list_checkpoints(
        run_id: str,
        include_snapshot: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return the checkpoint history for a run, sorted newest first."""
        keys = _scan_checkpoint_keys(run_id)
        checkpoints = []
        for key in keys:
            try:
                _, node_name, timestamp_ms = _parse_checkpoint_key(key)
            except ValueError:
                logging.warning("Skipping malformed checkpoint key: %s", key)
                continue

            item: Dict[str, Any] = {
                "checkpoint_id": key,
                "node_name": node_name,
                "timestamp": datetime.utcfromtimestamp(timestamp_ms / 1000.0).isoformat()
                + "Z",
                "timestamp_ms": timestamp_ms,
            }
            if include_snapshot:
                snapshot = _load_checkpoint_payload(key)
                if snapshot is not None:
                    item["state_snapshot"] = snapshot
            checkpoints.append(item)

        checkpoints.sort(key=lambda x: x["timestamp_ms"], reverse=True)
        return checkpoints

    @staticmethod
    def load_checkpoint(run_id: str, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        """Load the state snapshot stored at a checkpoint key."""
        return _load_checkpoint_payload(checkpoint_id)

    @staticmethod
    def fork_run(
        run_id: str,
        checkpoint_id: str,
        override_state: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        debug_mode: bool = False,
    ) -> str:
        """从检查点 fork 一个新的 run。

        流程：
          1. 加载检查点状态（深拷贝）
          2. 应用白名单覆盖字段（query/rewritten_query）
          3. 设置新 run 的元数据（_task_id、_parent_run_id、_replayed_from）
          4. 持久化新 run 的运行时状态和检查点
          5. 记录 Replay 依赖关系（保护活跃子 run 不被清理）
          6. 记录审计日志

        原 run 保持只读，所有变更写入新 run_id。
        """
        original_state = CheckpointService.load_checkpoint(run_id, checkpoint_id)
        if original_state is None:
            raise LookupError(f"Checkpoint not found: {checkpoint_id}")

        new_run_id = get_uuid()
        new_state = deepcopy(original_state)

        # Apply allowed overrides to the canonical state fields.
        override_state = override_state or {}
        sanitized_override: Dict[str, Any] = {}
        for key, value in override_state.items():
            if key in REPLAY_PROTECTED_FIELDS:
                continue
            if key in REPLAY_OVERRIDABLE_FIELDS:
                sanitized_override[key] = value
                new_state[key] = value
                # Mirror into Canvas globals form so the state can be applied directly.
                new_state[canvas_state_key(key)] = value
            else:
                logging.warning(
                    "Ignoring non-overridable replay field: %s", key
                )

        node_name = new_state.get("_node_name", "")
        timestamp_ms = int(time.time() * 1000)

        new_state.update(
            {
                "_task_id": new_run_id,
                "_parent_run_id": run_id,
                "_replayed_from": checkpoint_id,
                "_timestamp": time.time(),
                "_replayed_at": datetime.utcnow().isoformat() + "Z",
            }
        )

        effective_ttl_days = (
            CHECKPOINT_DEBUG_MODE_EXTENSION_DAYS
            if debug_mode or CHECKPOINT_DEBUG_MODE
            else CHECKPOINT_TTL_DAYS
        )
        checkpoint_ttl_seconds = effective_ttl_days * 24 * 3600

        _persist_runtime_state(new_run_id, new_state)
        _persist_checkpoint(
            new_run_id,
            node_name,
            timestamp_ms,
            new_state,
            checkpoint_ttl_seconds,
        )
        _record_replay_dependency(run_id, new_run_id)

        if user_id:
            CheckpointReplayAuditLogService.record(
                user_id=user_id,
                run_id=run_id,
                checkpoint_id=checkpoint_id,
                new_run_id=new_run_id,
                override_state=sanitized_override,
            )

        return new_run_id

    @staticmethod
    def cleanup_expired(
        ttl_days: Optional[int] = None,
        max_per_run: Optional[int] = None,
        keep_latest: Optional[int] = None,
        debug_mode_extension_days: Optional[int] = None,
    ) -> int:
        """执行检查点保留策略清理。

        清理策略（按优先级）：
          1. 始终保留最新 keep_latest 个检查点
          2. 保留 TTL 内的检查点（调试模式下使用更长的 TTL）
          3. 每个 run 最多保留 max_per_run 个
          4. 跳过有活跃 Replay 依赖的 run（防止清理正在使用的检查点）

        返回被删除的检查点数量。
        """
        ttl_days = ttl_days if ttl_days is not None else CHECKPOINT_TTL_DAYS
        max_per_run = max_per_run if max_per_run is not None else CHECKPOINT_MAX_PER_RUN
        keep_latest = keep_latest if keep_latest is not None else CHECKPOINT_KEEP_LATEST
        debug_mode_extension_days = (
            debug_mode_extension_days
            if debug_mode_extension_days is not None
            else CHECKPOINT_DEBUG_MODE_EXTENSION_DAYS
        )

        effective_ttl_seconds = (
            (debug_mode_extension_days if CHECKPOINT_DEBUG_MODE else ttl_days)
            * 24
            * 3600
        )
        now_ms = int(time.time() * 1000)

        client = _redis_client()
        if not client:
            return 0

        deleted = 0
        try:
            # Collect all checkpoint keys once, then group by run_id.
            all_keys: List[str] = list(client.scan_iter(match="*-checkpoint:*", count=500))
            runs: Dict[str, List[Tuple[str, int]]] = {}
            for key in all_keys:
                try:
                    run_id, _, ts = _parse_checkpoint_key(key)
                except ValueError:
                    continue
                runs.setdefault(run_id, []).append((key, ts))

            for run_id, parsed in runs.items():
                if _has_active_replay_dependencies(run_id):
                    continue

                # Newest first.
                parsed.sort(key=lambda x: x[1], reverse=True)
                preserve: Set[str] = set()

                # Always preserve the latest N checkpoints.
                for k, _ in parsed[: min(keep_latest, max_per_run)]:
                    preserve.add(k)

                # Preserve additional checkpoints that are within TTL and within the
                # max_per_run budget.
                for idx, (k, ts) in enumerate(parsed):
                    if idx >= max_per_run:
                        break
                    age_ms = now_ms - ts
                    if age_ms <= effective_ttl_seconds * 1000:
                        preserve.add(k)

                for k, _ in parsed:
                    if k in preserve:
                        continue
                    try:
                        client.delete(k)
                        deleted += 1
                    except Exception as e:
                        logging.warning("Failed to delete checkpoint %s: %s", k, e)
        except Exception as e:
            logging.warning("Checkpoint cleanup failed: %s", e)

        logging.info(
            "Checkpoint cleanup finished: deleted=%s ttl_days=%s max_per_run=%s keep_latest=%s",
            deleted,
            ttl_days,
            max_per_run,
            keep_latest,
        )
        return deleted


# ---------------------------------------------------------------------------
# Background cleanup scheduler
# ---------------------------------------------------------------------------

class CheckpointCleanupTask:
    """Light-weight background scheduler for checkpoint retention."""

    _started = False
    _lock = threading.Lock()

    @classmethod
    def run_once(cls) -> int:
        return CheckpointService.cleanup_expired()

    @classmethod
    def _loop(cls, interval_hours: int) -> None:
        interval_seconds = max(60, interval_hours * 3600)
        while True:
            try:
                time.sleep(interval_seconds)
                cls.run_once()
            except Exception as e:
                logging.warning("Checkpoint cleanup scheduler error: %s", e)

    @classmethod
    def start(cls, interval_hours: Optional[int] = None) -> None:
        """Start a daemon thread that runs cleanup hourly (or as configured)."""
        interval_hours = interval_hours or CHECKPOINT_CLEANUP_INTERVAL_HOURS
        with cls._lock:
            if cls._started:
                return
            cls._started = True

        thread = threading.Thread(
            target=cls._loop,
            args=(interval_hours,),
            name="checkpoint-cleanup-scheduler",
            daemon=True,
        )
        thread.start()
        logging.info(
            "Checkpoint cleanup scheduler started (interval=%sh)", interval_hours
        )
