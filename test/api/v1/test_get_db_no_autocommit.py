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
"""get_db 不得在请求正常结束时自动 commit。

通过 AST 静态检查 ``api/v1/core/database.py`` 的 ``get_db``，
避免导入时触发 create_async_engine / JWT 校验等副作用。
"""

import ast
import unittest
from pathlib import Path


class TestGetDbNoAutocommit(unittest.TestCase):
    """断言 get_db 事务边界归 Service 层所有。"""

    def test_get_db_source_has_no_commit(self):
        """get_db 源码中不得调用 session.commit。"""
        db_path = (
            Path(__file__).resolve().parents[3]
            / "api"
            / "v1"
            / "core"
            / "database.py"
        )
        source = db_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        get_db_fn = None
        for node in tree.body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "get_db":
                get_db_fn = node
                break

        self.assertIsNotNone(get_db_fn, "get_db function not found")

        commit_calls = [
            n
            for n in ast.walk(get_db_fn)
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "commit"
            )
        ]
        self.assertEqual(
            commit_calls,
            [],
            "get_db must not call commit(); service layer owns commits",
        )

    def test_get_db_source_rolls_back_on_exception(self):
        """get_db 异常路径必须调用 rollback。"""
        db_path = (
            Path(__file__).resolve().parents[3]
            / "api"
            / "v1"
            / "core"
            / "database.py"
        )
        source = db_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        get_db_fn = None
        for node in tree.body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "get_db":
                get_db_fn = node
                break

        self.assertIsNotNone(get_db_fn)

        rollback_calls = [
            n
            for n in ast.walk(get_db_fn)
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "rollback"
            )
        ]
        self.assertTrue(
            rollback_calls,
            "get_db must rollback on exception",
        )

        # 文档字符串应明确说明不自动 commit
        docstring = ast.get_docstring(get_db_fn) or ""
        self.assertIn("不再自动 commit", docstring)


if __name__ == "__main__":
    unittest.main()
