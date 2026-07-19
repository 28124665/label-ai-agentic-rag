#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
"""
繁简转换模块。

对应设计文档「双字段存储」章节中的繁简转换部分。

台湾繁体文本入库前需要转换为大陆简体，以便统一检索与存储。
本模块提供两层转换：
1. 字形转换：使用 OpenCC 的 t2s 配置进行繁体字形 -> 简体字形转换。
2. 词汇转换：将台湾地区用语替换为大陆地区用语（如「伺服器」->「服务器」、「軟體」->「软件」）。

为何使用 placeholder 保护专有名词：
    OpenCC 的 t2s 字典会对部分专有名词做字形转换，例如将「鴻海」转换为「鸿海」。
    但实际业务中，"鴻海" / "鴻海精密" 等是品牌专有名词，应原样保留。
    因此，在调用 OpenCC 之前，先用占位符（placeholder）替换白名单中的专有名词，
    字形/词汇转换完成后再恢复原词，避免专有名词被误转。

为何在 OpenCC 初始化时把「简体形式的 key」也合并进词汇表：
    OpenCC 的 t2s 配置只做字形转换（軟體 -> 软体），不会做词汇转换（软体 -> 软件）。
    若严格按「字形转换 -> 词汇替换」顺序执行，词汇替换阶段拿到的已经是简体字形文本，
    传统繁体 key（如「軟體」）将无法命中。因此在 OpenCC 加载成功后，对每个繁体 key
    调用一次 OpenCC.convert() 得到其简体字形（如「軟體」->「软体」），并把
    「简体 key -> 大陆用语」也加入合并词汇表，从而保证词汇替换阶段仍然可以命中。
    当 OpenCC 不可用时（如依赖未安装），退化为仅按繁体 key 做词汇替换，保持兜底可用。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class TraditionalToSimplifiedConverter:
    """繁体 -> 简体转换器。

    封装 OpenCC 字形转换 + 台湾用语词汇替换 + 专有名词白名单保护。
    OpenCC 实例懒加载，避免模块导入时就触发模型/字典加载。
    """

    def __init__(self, opencc_config: str = "t2s", tw_to_cn_path: str | None = None, proper_nouns_path: str | None = None):
        """初始化转换器。

        Args:
            opencc_config: OpenCC 配置名，默认 't2s'（繁体字形 -> 简体字形）。
            tw_to_cn_path: 台湾用语词典路径，默认 rag/res/tw_to_cn.json。
            proper_nouns_path: 专有名词白名单路径，默认 rag/res/proper_nouns.json。
        """
        self.opencc_config = opencc_config
        # 资源文件路径：rag/res/ 目录下存放台湾用语词典和专有名词白名单
        res_dir = Path(__file__).parent.parent / "res"
        self.tw_to_cn_path = Path(tw_to_cn_path) if tw_to_cn_path else res_dir / "tw_to_cn.json"
        self.proper_nouns_path = Path(proper_nouns_path) if proper_nouns_path else res_dir / "proper_nouns.json"

        # OpenCC 实例懒加载：避免模块导入时就触发字典加载，影响启动性能
        # 只有在第一次调用 convert() 时才真正初始化 OpenCC
        self._opencc = None
        self._opencc_initialized = False

        # 加载资源文件：台湾用语词典和专有名词白名单
        # 加载失败时返回空字典/列表，不会抛出异常，保证模块可用性
        self.tw_to_cn: dict[str, str] = self._load_tw_to_cn()
        self.proper_nouns: list[str] = self._load_proper_nouns()

        # 合并词汇表：原始繁体 key + （OpenCC 加载后补入的）简体 key
        # 为什么要合并两种 key？
        #   OpenCC t2s 只做字形转换（軟體 -> 软体），不会做词汇转换（软体 -> 软件）
        #   执行顺序：OpenCC 字形转换 -> 词汇替换
        #   字形转换后文本已经是简体（软体），但原始词汇表只有繁体 key（軟體）
        #   如果不合并简体 key，词汇替换阶段就无法命中「软体」，导致台湾用语无法转换
        #   因此在 OpenCC 初始化时，对每个繁体 key 调用 OpenCC.convert() 得到简体字形
        #   并把「简体 key -> 大陆用语」也加入词汇表
        self._vocab_merged: dict[str, str] = dict(self.tw_to_cn)
        # 按 key 长度降序排序：避免短 key 误替换长 key 中的子串
        # 例如：「程式碼」(3字) 必须优先于「程式」(2字) 匹配
        # 如果不按长度排序，「程式碼」可能被「程式」先替换成「程序碼」，导致错误
        self._sorted_vocab_keys: list[str] = sorted(self._vocab_merged.keys(), key=len, reverse=True)
        # 专有名词同样按长度降序：优先匹配更长名词
        # 例如：「鴻海精密」(4字) 必须优先于「鴻海」(2字) 匹配
        # 如果不按长度排序，「鴻海精密」可能被「鴻海」先替换成占位符，破坏完整性
        self._sorted_proper_nouns: list[str] = sorted([n for n in self.proper_nouns if n], key=len, reverse=True)

    def _load_tw_to_cn(self) -> dict[str, str]:
        """加载台湾用语 -> 大陆用语词典。加载失败返回空字典并告警。"""
        try:
            with open(self.tw_to_cn_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("tw_to_cn.json content is not a dict, got %s", type(data).__name__)
                return {}
            return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            logger.warning("Fail to load tw_to_cn from %s: %s", self.tw_to_cn_path, e)
            return {}

    def _load_proper_nouns(self) -> list[str]:
        """加载专有名词白名单。支持 {"proper_nouns": [...]} 或 [...] 两种结构。"""
        try:
            with open(self.proper_nouns_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                nouns = data.get("proper_nouns", [])
            elif isinstance(data, list):
                nouns = data
            else:
                logger.warning("proper_nouns.json content type unexpected: %s", type(data).__name__)
                return []
            return [str(n) for n in nouns if n]
        except Exception as e:
            logger.warning("Fail to load proper_nouns from %s: %s", self.proper_nouns_path, e)
            return []

    def _get_opencc(self):
        """懒加载 OpenCC 实例，并将繁体 key 的简体字形补入合并词汇表。

        Returns:
            opencc.OpenCC 实例；若依赖未安装或初始化失败则返回 None，由上层走兜底逻辑。
        """
        # 幂等性保证：只初始化一次，后续调用直接返回缓存实例
        if self._opencc_initialized:
            return self._opencc
        self._opencc_initialized = True
        try:
            import opencc

            # 关键修复：opencc-python-reimplemented 内部会自动追加 .json 后缀
            # 如果调用方传入 "t2s.json"，OpenCC 会尝试加载 "t2s.json.json"，导致 FileNotFoundError
            # 因此这里需要主动去除 .json 后缀，让 OpenCC 自己追加
            config = self.opencc_config
            if config.endswith(".json"):
                config = config[: -len(".json")]
            self._opencc = opencc.OpenCC(config)
            # 关键设计：将每个繁体 key 的简体字形也加入合并词汇表
            # 作用：保证 OpenCC 字形转换后的简体文本仍能命中词汇替换
            # 举例：
            #   原始词汇表：{"軟體": "软件"}
            #   OpenCC 字形转换后：文本中的「軟體」变成「软体」
            #   如果不合并简体 key，词汇替换阶段找不到「软体」，台湾用语转换失败
            #   合并后：{"軟體": "软件", "软体": "软件"}
            #   这样「软体」就能被正确替换为「软件」
            for k, v in self.tw_to_cn.items():
                k_simp = self._opencc.convert(k)
                # 过滤条件：简体字形有效、与繁体不同、与目标值不同、尚未在词汇表中
                if k_simp and k_simp != k and k_simp != v and k_simp not in self._vocab_merged:
                    self._vocab_merged[k_simp] = v
            # 重新排序：合并新 key 后需要重新按长度降序排序
            self._sorted_vocab_keys = sorted(self._vocab_merged.keys(), key=len, reverse=True)
            logger.info("OpenCC initialized with config=%s, merged vocab size=%d", config, len(self._vocab_merged))
        except Exception as e:
            # 兜底策略：OpenCC 初始化失败时（如依赖未安装），退化为仅词汇替换模式
            # 虽然字形转换会失效，但台湾用语词汇替换仍可进行，保证基本功能可用
            logger.warning("Fail to initialize OpenCC (config=%s): %s. Fallback to vocabulary-only conversion.", self.opencc_config, e)
            self._opencc = None
        return self._opencc

    def _protect_proper_nouns(self, text: str) -> tuple[str, dict[str, str]]:
        """用占位符替换专有名词，避免后续转换误伤品牌词等。

        Args:
            text: 原始文本。

        Returns:
            tuple (protected_text, mapping)：
              - protected_text: 已用占位符替换专有名词的文本；
              - mapping: {placeholder: original_noun}，供 _restore_proper_nouns 还原。
        """
        mapping: dict[str, str] = {}
        if not self._sorted_proper_nouns:
            return text, mapping
        # 按长度降序遍历专有名词：保证长名词优先匹配
        # 例如：「鴻海精密」(4字) 优先于「鴻海」(2字)
        # 如果不按长度排序，「鴻海精密」中的「鴻海」可能先被替换，破坏完整性
        for idx, noun in enumerate(self._sorted_proper_nouns):
            if noun not in text:
                continue
            # 占位符格式：\x00PN_{idx}\x00
            # 为什么用 \x00（NULL 字符）？
            #   1. 正常文本中不会出现 NULL 字符，避免冲突
            #   2. OpenCC 不会转换 NULL 字符，保证占位符在字形转换中保持不变
            #   3. 前后都用 \x00 包裹，形成明确边界，避免被其他替换逻辑误伤
            placeholder = f"\x00PN_{idx}\x00"
            mapping[placeholder] = noun
            text = text.replace(noun, placeholder)
        return text, mapping

    def _restore_proper_nouns(self, text: str, mapping: dict[str, str]) -> str:
        """将占位符还原为原始专有名词。"""
        if not mapping:
            return text
        for placeholder, original in mapping.items():
            text = text.replace(placeholder, original)
        return text

    def _convert_vocabulary(self, text: str) -> str:
        """按合并词汇表做台湾用语 -> 大陆用语替换。

        按 key 长度降序遍历，避免短 key 误命中长 key 的子串。
        """
        if not self._sorted_vocab_keys:
            return text
        for k in self._sorted_vocab_keys:
            if k in text:
                text = text.replace(k, self._vocab_merged[k])
        return text

    def convert(self, text: str) -> str:
        """将繁体中文转换为简体中文。

        转换流程：
          1. 用占位符保护专有名词白名单中的词；
          2. 调用 OpenCC 做字形转换（t2s），失败则跳过；
          3. 按合并词汇表做台湾用语 -> 大陆用语替换；
          4. 还原专有名词占位符。

        Args:
            text: 待转换文本，可为繁体/简体/英文混合。

        Returns:
            转换后的简体文本；输入为空则原样返回。
        """
        if not text:
            return text

        # 步骤 1：保护专有名词
        # 作用：避免 OpenCC 字形转换或词汇替换时误伤品牌词（如「鴻海」被转成「鸿海」）
        # 实现：用 \x00PN_{idx}\x00 格式的占位符替换专有名词，转换完成后再还原
        protected, mapping = self._protect_proper_nouns(text)

        # 步骤 2：OpenCC 字形转换
        # 作用：将繁体字形转换为简体字形（如「供應鏈」->「供应链」）
        # 兜底：如果 OpenCC 初始化失败（依赖未安装），跳过字形转换，仅做词汇替换
        converter = self._get_opencc()
        if converter is not None:
            converted = converter.convert(protected)
        else:
            converted = protected

        # 步骤 3：台湾用语词汇替换
        # 作用：将台湾地区用语替换为大陆地区用语（如「伺服器」->「服务器」）
        # 关键：使用合并词汇表（包含繁体 key 和简体 key），保证字形转换后的文本仍能命中
        converted = self._convert_vocabulary(converted)

        # 步骤 4：恢复专有名词
        # 作用：将占位符还原为原始专有名词，保证品牌词等不被转换
        return self._restore_proper_nouns(converted, mapping)


# 模块级单例：供外部直接调用，避免重复加载资源
default_converter = TraditionalToSimplifiedConverter()


def convert(text: str) -> str:
    """模块级便捷函数：使用默认单例转换繁体 -> 简体。

    等价于 ``default_converter.convert(text)``。
    """
    return default_converter.convert(text)
