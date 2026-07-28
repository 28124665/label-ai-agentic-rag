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
"""Web Tool 封装模块 - LangGraph 版本。

将现有的 Web 搜索流程封装为独立的工具类，供 LangGraph 调用。
支持 Tavily 和 DuckDuckGo 两种搜索引擎。

参考原有实现：
- agent/tools/tavily.py: TavilySearch 组件
- agent/tools/duckduckgo.py: DuckDuckGo 组件
"""

import logging
import time
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)


class WebToolInput(TypedDict, total=False):
    """Web Tool 输入定义。"""

    query: str  # 搜索查询
    query_lang: Literal["zh_CN", "zh_TW", "en"]  # 查询语言
    search_engine: Literal["tavily", "duckduckgo"]  # 搜索引擎，默认 "tavily"
    max_results: int  # 最大结果数，默认 6
    search_depth: Literal["basic", "advanced"]  # 搜索深度，默认 "basic"
    topic: Literal["general", "news"]  # 搜索主题，默认 "general"
    include_domains: list[str]  # 包含的域名列表
    exclude_domains: list[str]  # 排除的域名列表
    api_key: str  # API 密钥（Tavily 需要）


class WebToolOutput(TypedDict, total=False):
    """Web Tool 输出定义。"""

    docs: list[dict]  # 搜索到的文档列表 [{content, url, title, score}]
    urls: list[str]  # 搜索到的 URL 列表
    search_engine: str  # 使用的搜索引擎
    result_count: int  # 结果数量
    search_time_ms: int  # 搜索耗时（毫秒）
    error_message: str  # 错误信息（如有）


class WebTool:
    """Web 搜索工具 - LangGraph 版本。

    将现有的 Web 搜索流程封装为独立工具，不依赖 Canvas。
    支持 Tavily 和 DuckDuckGo 两种搜索引擎。
    """

    def __init__(self):
        """初始化 WebTool。"""
        self.component_name = "WebTool"

    async def invoke(self, input_data: WebToolInput) -> WebToolOutput:
        """执行 Web 搜索流程。

        Args:
            input_data: Web Tool 输入参数

        Returns:
            WebToolOutput: 搜索结果，包含文档列表、URL 列表等
        """
        start_time = time.time()

        # 提取参数（带默认值）
        query = input_data.get("query", "")
        search_engine = input_data.get("search_engine", "tavily")
        max_results = input_data.get("max_results", 6)
        search_depth = input_data.get("search_depth", "basic")
        topic = input_data.get("topic", "general")
        include_domains = input_data.get("include_domains", [])
        exclude_domains = input_data.get("exclude_domains", [])
        api_key = input_data.get("api_key", "")

        if not query:
            return self._empty_result(start_time, "query 为空")

        logger.info(f"[WebTool] 开始搜索: query='{query}', engine={search_engine}")

        # 根据搜索引擎类型调用不同的搜索实现
        try:
            if search_engine == "tavily":
                docs = await self._search_tavily(
                    query=query,
                    max_results=max_results,
                    search_depth=search_depth,
                    topic=topic,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                    api_key=api_key,
                )
            elif search_engine == "duckduckgo":
                docs = await self._search_duckduckgo(
                    query=query,
                    max_results=max_results,
                    topic=topic,
                )
            else:
                return self._empty_result(start_time, f"不支持的搜索引擎: {search_engine}")

        except Exception as e:
            logger.error(f"[WebTool] 搜索失败: {e}")
            return self._empty_result(start_time, f"搜索失败: {e}")

        # 提取 URL 列表
        urls = [doc.get("url", "") for doc in docs if doc.get("url")]

        result_count = len(docs)
        search_time_ms = int((time.time() - start_time) * 1000)

        logger.info(f"[WebTool] 搜索完成: results={result_count}, time={search_time_ms}ms")

        return WebToolOutput(
            docs=docs,
            urls=urls,
            search_engine=search_engine,
            result_count=result_count,
            search_time_ms=search_time_ms,
            error_message="",
        )

    async def _search_tavily(
        self,
        query: str,
        max_results: int,
        search_depth: str,
        topic: str,
        include_domains: list[str],
        exclude_domains: list[str],
        api_key: str,
    ) -> list[dict]:
        """使用 Tavily 搜索引擎进行搜索。

        Args:
            query: 搜索查询
            max_results: 最大结果数
            search_depth: 搜索深度
            topic: 搜索主题
            include_domains: 包含的域名列表
            exclude_domains: 排除的域名列表
            api_key: API 密钥

        Returns:
            list[dict]: 搜索结果列表
        """
        try:
            from tavily import TavilyClient
        except ImportError:
            raise Exception("Tavily 客户端未安装，请运行: pip install tavily-python")

        if not api_key:
            raise Exception("Tavily API 密钥未配置")

        # 创建 Tavily 客户端
        tavily_client = TavilyClient(api_key=api_key)

        # 构建搜索参数
        search_params = {
            "query": query,
            "search_depth": search_depth,
            "topic": topic,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }

        if include_domains:
            search_params["include_domains"] = include_domains
        if exclude_domains:
            search_params["exclude_domains"] = exclude_domains

        # 执行搜索
        res = tavily_client.search(**search_params)

        # 格式化结果
        docs = []
        for result in res.get("results", []):
            doc = {
                "content": result.get("raw_content") or result.get("content", ""),
                "url": result.get("url", ""),
                "title": result.get("title", ""),
                "score": result.get("score", 0.0),
            }
            docs.append(doc)

        return docs

    async def _search_duckduckgo(
        self,
        query: str,
        max_results: int,
        topic: str,
    ) -> list[dict]:
        """使用 DuckDuckGo 搜索引擎进行搜索。

        Args:
            query: 搜索查询
            max_results: 最大结果数
            topic: 搜索主题（general/news）

        Returns:
            list[dict]: 搜索结果列表
        """
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            raise Exception("DuckDuckGo 客户端未安装，请运行: pip install duckduckgo-search")

        docs = []

        with DDGS() as ddgs:
            if topic == "general":
                # 通用搜索
                duck_res = ddgs.text(query, max_results=max_results)
                for result in duck_res:
                    doc = {
                        "content": result.get("body", ""),
                        "url": result.get("href", result.get("url", "")),
                        "title": result.get("title", ""),
                        "score": 0.0,  # DuckDuckGo 不提供分数
                    }
                    docs.append(doc)
            elif topic == "news":
                # 新闻搜索
                duck_res = ddgs.news(query, max_results=max_results)
                for result in duck_res:
                    doc = {
                        "content": result.get("body", ""),
                        "url": result.get("href", result.get("url", "")),
                        "title": result.get("title", ""),
                        "score": 0.0,  # DuckDuckGo 不提供分数
                    }
                    docs.append(doc)
            else:
                raise Exception(f"不支持的 DuckDuckGo 主题: {topic}")

        return docs

    def _empty_result(self, start_time: float, error_message: str = "") -> WebToolOutput:
        """返回空结果。

        Args:
            start_time: 开始时间戳
            error_message: 错误信息

        Returns:
            WebToolOutput: 空的搜索结果
        """
        search_time_ms = int((time.time() - start_time) * 1000)
        return WebToolOutput(
            docs=[],
            urls=[],
            search_engine="tavily",
            result_count=0,
            search_time_ms=search_time_ms,
            error_message=error_message,
        )


# 全局实例（可选，方便 LangGraph 节点调用）
_web_tool_instance: WebTool | None = None


def get_web_tool() -> WebTool:
    """获取 WebTool 单例实例。"""
    global _web_tool_instance
    if _web_tool_instance is None:
        _web_tool_instance = WebTool()
    return _web_tool_instance
