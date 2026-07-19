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
"""语言检测模块。

对应设计文档「查询预处理与语言路由」章节中的语言检测部分。

提供 :func:`detect_language` 函数，基于字符比例统计判定输入文本的语言，
返回以下三种标识之一：

    - :data:`LANG_ZH_SIMPLIFIED`（``"zh-simplified"``）：简体中文
    - :data:`LANG_ZH_TRADITIONAL`（``"zh-traditional"``）：繁体中文
    - :data:`LANG_EN`（``"en"``）：英文

判定规则
--------

1. 空文本或纯空白 → 返回 :data:`LANG_ZH_SIMPLIFIED`（默认值）。
2. 遍历文本统计以下字符数：

   - ``en_chars``：英文字母（仅 a-zA-Z）数量
   - ``total_cjk``：CJK 统一表意文字数量（Unicode 范围 ``\\u4e00-\\u9fff``）
   - ``trad_chars``：命中「繁体特征字」集合的字符数量

3. 判定逻辑（按优先级）：

   - 若 ``en_chars / (en_chars + total_cjk) > 0.5`` → 返回 :data:`LANG_EN`
   - 否则若 ``total_cjk > 0`` 且 ``trad_chars / total_cjk > 0.3`` →
     返回 :data:`LANG_ZH_TRADITIONAL`
   - 否则 → 返回 :data:`LANG_ZH_SIMPLIFIED`

判定阈值的选择依据
------------------

- **英文判定阈值 0.5**：当英文字母在「英文字母 + 中文字符」中占比超过一半时，
  视为以英文为主的文本，避免在英文为主、夹杂少量中文术语时误判为中文。
- **繁体特征字比例阈值 30%**：经验值。繁简体在常用字上有大量重叠，
  仅靠「繁体特征字」（即简体中文中不使用的字）才能区分。30% 的阈值可避免
  少量繁体字（如引文、人名）混入简体文本时的误判，同时对真正的繁体文本
  （特征字密度通常 > 50%）有足够区分度。

性能考虑
--------

- 特征字集合使用 :class:`frozenset`，模块加载时构建一次，避免重复创建。
- 仅依赖 Python 标准库，无外部依赖。
"""

LANG_ZH_SIMPLIFIED = "zh-simplified"
LANG_ZH_TRADITIONAL = "zh-traditional"
LANG_EN = "en"

# 繁体特征字集合：这些字在简体中文中不使用，出现即强烈提示繁体文本。
# 涵盖供应链、企业竞争、IT 硬件、软件、通信、AI 等领域的典型繁体用字。
_TRADITIONAL_CHARS = frozenset(
    "供應鏈企業競爭優勢腦體網資記憶體伺服器專案品管螢幕程式碼檔案資訊訊號頻寬匯流排預設視窗滑鼠鍵盤硬碟光碟印表機掃描數位類比影像視訊音訊頻道頻率雲端巨量據物聯網人工智慧機器學習深度神經網"
)

# CJK 统一表意文字范围（U+4E00 – U+9FFF）
_CJK_MIN = 0x4E00
_CJK_MAX = 0x9FFF


def detect_language(text: str) -> str:
    """检测输入文本的语言，返回语言标识。

    Args:
        text: 待检测的文本，允许空字符串或纯空白字符串。

    Returns:
        三种标识之一：:data:`LANG_ZH_SIMPLIFIED`、:data:`LANG_ZH_TRADITIONAL`、
        :data:`LANG_EN`。空文本或纯空白返回 :data:`LANG_ZH_SIMPLIFIED`（默认值）。

    判定细节见模块文档字符串。
    """
    # 空文本兜底：默认返回简体中文
    # 原因：RAG 系统主要服务中文用户，简体中文是默认语言
    if not text or not text.strip():
        return LANG_ZH_SIMPLIFIED

    # 统计三类字符数量：
    # - en_chars：英文字母数（用于判断是否为英文为主的文本）
    # - total_cjk：中文字符总数（用于计算繁体特征字占比的分母）
    # - trad_chars：繁体特征字数（用于判断是否为繁体文本）
    en_chars = 0
    total_cjk = 0
    trad_chars = 0

    # 单次遍历统计：性能优化，避免多次遍历文本
    for ch in text:
        # 英文字母统计：仅统计 a-zA-Z，不统计数字、标点等
        # 作用：判断文本是否以英文为主
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            en_chars += 1
            continue

        # CJK 统一表意文字统计：Unicode 范围 U+4E00 – U+9FFF
        # 作用：统计中文字符总数，作为繁体特征字占比的分母
        if _CJK_MIN <= ord(ch) <= _CJK_MAX:
            total_cjk += 1
            # 繁体特征字检测：如果该字在繁体特征字集合中，计数器加 1
            # 作用：繁体特征字是简体中文中不使用的字，出现即强烈提示繁体文本
            if ch in _TRADITIONAL_CHARS:
                trad_chars += 1

    # 判定规则 1：英文判定
    # 公式：en_chars / (en_chars + total_cjk) > 0.5
    # 作用：当英文字母在「英文 + 中文」中占比超过一半时，判定为英文
    # 为什么用这个公式？
    #   1. 分母是「英文 + 中文」，排除数字、标点等干扰
    #   2. 阈值 0.5 表示英文占多数，避免在英文为主、夹杂少量中文术语时误判为中文
    #   3. 例如："Supply chain management 供应链" -> en_chars=24, total_cjk=3 -> 24/27=0.89 > 0.5 -> 英文
    denom = en_chars + total_cjk
    if denom > 0 and en_chars / denom > 0.5:
        return LANG_EN

    # 判定规则 2：繁体判定
    # 公式：trad_chars / total_cjk > 0.3
    # 作用：在中文文本中，繁体特征字占比超过 30% 时，判定为繁体
    # 为什么阈值是 0.3？
    #   1. 繁简体在常用字上有大量重叠，仅靠繁体特征字才能区分
    #   2. 30% 是经验值：避免少量繁体字（如引文、人名）混入简体文本时的误判
    #   3. 真正的繁体文本特征字密度通常 > 50%，30% 阈值有足够区分度
    #   4. 例如："供應鏈管理" -> total_cjk=5, trad_chars=3 (供/應/鏈) -> 3/5=0.6 > 0.3 -> 繁体
    if total_cjk > 0 and trad_chars / total_cjk > 0.3:
        return LANG_ZH_TRADITIONAL

    # 判定规则 3：默认返回简体
    # 作用：排除英文和繁体后，剩余情况都视为简体
    # 包括：纯简体文本、简繁混合但繁体占比 <= 30%、无中文字符的文本等
    return LANG_ZH_SIMPLIFIED
