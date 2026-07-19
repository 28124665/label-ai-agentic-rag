import asyncio, json, sys, types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import agent
_component_pkg = types.ModuleType('agent.component')
_component_pkg.__path__ = [str(Path('agent/component').absolute())]
sys.modules['agent.component'] = _component_pkg
_canvas_mod = types.ModuleType('agent.canvas')
class Graph: pass
_canvas_mod.Graph = Graph
sys.modules['agent.canvas'] = _canvas_mod
_llm_service_stub = types.ModuleType('api.db.services.llm_service')
_llm_service_stub.LLMBundle = type('LLMBundle', (), {})
sys.modules['api.db.services.llm_service'] = _llm_service_stub
_tenant_model_stub = types.ModuleType('api.db.joint_services.tenant_model_service')
def _stub(*a,**k): return {}
_tenant_model_stub.get_model_config_by_type_and_name = _stub
_tenant_model_stub.get_tenant_default_model_by_type = _stub
sys.modules['api.db.joint_services.tenant_model_service'] = _tenant_model_stub
from agent.component.grader import Grader, GraderParam
MockCanvas = type('MockCanvas', (Graph, MagicMock), {})
canvas = MockCanvas()
print('canvas type:', type(canvas))
print('isinstance Graph:', isinstance(canvas, Graph))
from agent.canvas import Graph as G2
print('isinstance G2:', isinstance(canvas, G2))
param = GraderParam()
param.llm_id='x'
param.check()
try:
    g = Grader(canvas, 'g0', param)
    print('init ok')
except AssertionError as e:
    print('init failed:', e)
