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

"""Component-level test stubs to avoid heavy/optional runtime dependencies."""

import sys
import types


def _install_xgboost_stub():
    """Stub xgboost so deepdoc parsers can be imported without pkg_resources."""
    if "xgboost" in sys.modules:
        return

    stub = types.ModuleType("xgboost")

    class _DMatrix:
        def __init__(self, *args, **kwargs):
            pass

    class _DeviceQuantileDMatrix(_DMatrix):
        pass

    class _Booster:
        pass

    class _DataIter:
        pass

    def _build_info():
        return {}

    stub.DMatrix = _DMatrix
    stub.DeviceQuantileDMatrix = _DeviceQuantileDMatrix
    stub.Booster = _Booster
    stub.DataIter = _DataIter
    stub.build_info = _build_info
    stub.xgb = stub

    sys.modules["xgboost"] = stub


def _install_nltk_stub():
    """Stub nltk to prevent network downloads during import."""
    if "nltk" in sys.modules:
        return

    nltk_stub = types.ModuleType("nltk")
    nltk_stub.download = lambda *args, **kwargs: None

    data_stub = types.ModuleType("nltk.data")
    data_stub.find = lambda *args, **kwargs: ""
    data_stub.load = lambda *args, **kwargs: None
    nltk_stub.data = data_stub

    sys.modules["nltk"] = nltk_stub
    sys.modules["nltk.data"] = data_stub


_install_xgboost_stub()
_install_nltk_stub()
