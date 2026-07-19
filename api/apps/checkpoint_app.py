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

from quart import request

from api.apps import current_user, login_required
from api.db.services.canvas_service import UserCanvasService
from api.db.services.checkpoint_service import (
    CheckpointCleanupTask,
    CheckpointService,
)
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    get_request_json,
    server_error_response,
    validate_request,
)
from common.constants import RetCode

"""检查点时间旅行 API 路由。

对应需求：P2-FR-05（检查点时间旅行）

功能说明：
  提供检查点历史查询、Replay（从检查点 fork 新 run）、清理等 REST API。

实现方式：
  1. GET /<run_id>：查询指定 run 的检查点历史列表
     - 支持 include_snapshot 参数控制是否返回状态快照
     - 校验当前用户对 Canvas 的所有权
  2. POST /replay：从指定检查点 Replay
     - 加载检查点状态 -> 校验 Canvas 所有权 -> fork 新 run
     - 支持 override_state 覆盖白名单字段（仅 query/rewritten_query）
     - 禁止覆盖 user_id/tenant_id 等保护字段
     - 记录审计日志
  3. DELETE /cleanup：触发检查点清理（仅管理员）
     - 支持传入自定义保留策略参数
  4. 模块导入时自动启动后台清理调度器（可通过 CHECKPOINT_CLEANUP_SCHEDULER=false 禁用）
"""


@manager.route('/<run_id>', methods=['GET'])  # noqa: F821
@login_required
def list_checkpoints(run_id):
    """查询指定 run 的检查点历史列表。

    返回按时间倒序排列的检查点列表，每个检查点包含：
    checkpoint_id、node_name、timestamp，可选包含 state_snapshot。
    首先校验当前用户对该 Canvas 的所有权。
    """
    try:
        include_snapshot = (
            request.args.get("include_snapshot", "false").lower() == "true"
        )
        checkpoints = CheckpointService.list_checkpoints(
            run_id, include_snapshot=include_snapshot
        )

        # Ownership check: a checkpoint stores the canvas_id it belongs to.
        if checkpoints:
            snapshot = CheckpointService.load_checkpoint(
                run_id, checkpoints[0]["checkpoint_id"]
            )
            canvas_id = snapshot.get("_canvas_id") if snapshot else None
            if canvas_id and not UserCanvasService.accessible(
                canvas_id, current_user.id
            ):
                return get_json_result(
                    data=False,
                    message="Only owner of the canvas authorized for this operation.",
                    code=RetCode.OPERATING_ERROR,
                )

        return get_json_result(data={"checkpoints": checkpoints})
    except Exception as e:
        return server_error_response(e)


@manager.route('/replay', methods=['POST'])  # noqa: F821
@validate_request("from_checkpoint")
@login_required
async def replay():
    """从指定检查点 Replay，fork 一个新的 run。

    流程：
      1. 加载检查点状态
      2. 校验 Canvas 所有权
      3. 调用 CheckpointService.fork_run 创建新 run
      4. 返回新 run_id、原 run_id、检查点 ID

    安全约束：
      - 只允许覆盖白名单字段（query、rewritten_query）
      - 禁止覆盖 user_id、tenant_id 等保护字段
      - 每次 Replay 记录审计日志
    """
    try:
        req = await get_request_json()
        checkpoint_id = req["from_checkpoint"]
        override_state = req.get("override_state") or {}

        state = CheckpointService.load_checkpoint("", checkpoint_id)
        if not state:
            return get_data_error_result(message="Checkpoint not found.")

        canvas_id = state.get("_canvas_id")
        if canvas_id and not UserCanvasService.accessible(canvas_id, current_user.id):
            return get_json_result(
                data=False,
                message="Only owner of the canvas authorized for this operation.",
                code=RetCode.OPERATING_ERROR,
            )

        parent_run_id = state.get("_task_id", "")
        new_run_id = CheckpointService.fork_run(
            run_id=parent_run_id,
            checkpoint_id=checkpoint_id,
            override_state=override_state,
            user_id=str(current_user.id),
        )

        return get_json_result(
            data={
                "new_run_id": new_run_id,
                "parent_run_id": parent_run_id,
                "checkpoint_id": checkpoint_id,
            }
        )
    except Exception as e:
        return server_error_response(e)


@manager.route('/cleanup', methods=['DELETE'])  # noqa: F821
@login_required
async def cleanup():
    """触发检查点保留策略清理（仅管理员可操作）。

    支持通过请求体传入自定义清理参数：
    ttl_days、max_per_run、keep_latest、debug_mode_extension_days。
    返回被删除的检查点数量。
    """
    try:
        if not getattr(current_user, "is_superuser", False):
            return get_json_result(
                data=False,
                message="Admin permission required.",
                code=RetCode.OPERATING_ERROR,
            )

        req = await get_request_json() or {}
        deleted = CheckpointService.cleanup_expired(
            ttl_days=req.get("ttl_days"),
            max_per_run=req.get("max_per_run"),
            keep_latest=req.get("keep_latest"),
            debug_mode_extension_days=req.get("debug_mode_extension_days"),
        )
        return get_json_result(data={"deleted": deleted})
    except Exception as e:
        return server_error_response(e)


# Start the background cleanup scheduler unless explicitly disabled.
if os.environ.get("CHECKPOINT_CLEANUP_SCHEDULER", "true").lower() != "false":
    CheckpointCleanupTask.start()
