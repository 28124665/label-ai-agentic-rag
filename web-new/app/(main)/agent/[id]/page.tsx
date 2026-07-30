'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, Save, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ToolConfig } from '@/components/agent/tool-config';
import { RoutingStrategy } from '@/components/agent/routing-strategy';
import { WorkflowVisualizer } from '@/components/agent/workflow-visualizer';
import { agentService, Agent } from '@/services/agent.service';

export default function AgentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const agentId = params.id as string;

  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [toolsConfig, setToolsConfig] = useState<any>({});
  const [routingConfig, setRoutingConfig] = useState<any>({});
  const [degradationConfig, setDegradationConfig] = useState<any>({});

  useEffect(() => {
    loadAgent();
  }, [agentId]);

  const loadAgent = async () => {
    try {
      const data = await agentService.get(agentId);
      setAgent(data);
      setName(data.name);
      setDescription(data.description || '');
      setToolsConfig(data.tools_config || {});
      setRoutingConfig(data.routing_config || {});
      setDegradationConfig(data.degradation_config || {
        max_retries: 3,
        fallback: 'default',
        timeout_seconds: 30,
      });
    } catch (error) {
      console.error('Failed to load agent:', error);
      router.push('/agent');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const updated = await agentService.update(agentId, {
        name,
        description,
        tools_config: toolsConfig,
        routing_config: routingConfig,
        degradation_config: degradationConfig,
      });
      setAgent(updated);
      alert('保存成功');
    } catch (error) {
      console.error('Failed to save agent:', error);
      alert('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm('确定要删除该 Agent 吗？')) return;
    try {
      await agentService.delete(agentId);
      router.push('/agent');
    } catch (error) {
      console.error('Failed to delete agent:', error);
    }
  };

  const handleToggleActive = async () => {
    if (!agent) return;
    try {
      const updated = await agentService.update(agentId, {
        is_active: !agent.is_active,
      });
      setAgent(updated);
    } catch (error) {
      console.error('Failed to toggle agent:', error);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-gray-400">加载中...</div>
      </div>
    );
  }

  if (!agent) return null;

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)]">
      {/* Header */}
      <div className="h-14 border-b bg-white flex items-center px-4 gap-4 flex-shrink-0">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => router.push('/agent')}
        >
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <div className="flex-1 font-medium">{agent.name}</div>
        <div
          className={`px-2 py-0.5 text-xs rounded-full ${
            agent.is_active
              ? 'bg-green-100 text-green-700'
              : 'bg-gray-100 text-gray-500'
          }`}
        >
          {agent.is_active ? '已启用' : '未启用'}
        </div>
        <Button variant="outline" size="sm" onClick={handleToggleActive}>
          {agent.is_active ? '停用' : '启用'}
        </Button>
        <Button size="sm" onClick={handleSave} disabled={saving}>
          <Save className="h-4 w-4 mr-1" />
          {saving ? '保存中...' : '保存'}
        </Button>
        <Button variant="destructive" size="sm" onClick={handleDelete}>
          <Trash2 className="h-4 w-4 mr-1" />
          删除
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        <div className="max-w-5xl mx-auto space-y-6">
          {/* Basic Info */}
          <section className="bg-white rounded-lg border p-6">
            <h2 className="text-lg font-semibold mb-4">基本信息</h2>
            <div className="space-y-4">
              <div>
                <Label htmlFor="name">名称</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Agent 名称"
                  className="mt-1"
                />
              </div>
              <div>
                <Label htmlFor="description">描述</Label>
                <Textarea
                  id="description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Agent 描述"
                  className="mt-1"
                  rows={3}
                />
              </div>
            </div>
          </section>

          {/* Tool Config */}
          <section className="bg-white rounded-lg border p-6">
            <h2 className="text-lg font-semibold mb-4">工具配置</h2>
            <ToolConfig
              config={toolsConfig}
              onChange={setToolsConfig}
            />
          </section>

          {/* Routing Strategy */}
          <section className="bg-white rounded-lg border p-6">
            <h2 className="text-lg font-semibold mb-4">路由策略</h2>
            <RoutingStrategy
              config={routingConfig}
              onChange={setRoutingConfig}
            />
          </section>

          {/* Degradation Strategy */}
          <section className="bg-white rounded-lg border p-6">
            <h2 className="text-lg font-semibold mb-4">降级策略</h2>
            <div className="space-y-4">
              <div>
                <Label htmlFor="maxRetries">最大重试次数</Label>
                <Input
                  id="maxRetries"
                  type="number"
                  min={0}
                  max={10}
                  value={degradationConfig.max_retries ?? 3}
                  onChange={(e) =>
                    setDegradationConfig({
                      ...degradationConfig,
                      max_retries: parseInt(e.target.value) || 0,
                    })
                  }
                  className="mt-1"
                />
              </div>
              <div>
                <Label htmlFor="timeout">超时时间（秒）</Label>
                <Input
                  id="timeout"
                  type="number"
                  min={1}
                  max={300}
                  value={degradationConfig.timeout_seconds ?? 30}
                  onChange={(e) =>
                    setDegradationConfig({
                      ...degradationConfig,
                      timeout_seconds: parseInt(e.target.value) || 30,
                    })
                  }
                  className="mt-1"
                />
              </div>
              <div>
                <Label htmlFor="fallback">降级策略</Label>
                <select
                  id="fallback"
                  value={degradationConfig.fallback ?? 'default'}
                  onChange={(e) =>
                    setDegradationConfig({
                      ...degradationConfig,
                      fallback: e.target.value,
                    })
                  }
                  className="mt-1 w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value="default">使用默认 Agent</option>
                  <option value="retry">持续重试</option>
                  <option value="skip">跳过工具调用</option>
                  <option value="error">直接返回错误</option>
                </select>
              </div>
            </div>
          </section>

          {/* Workflow Visualizer */}
          <section className="bg-white rounded-lg border p-6">
            <h2 className="text-lg font-semibold mb-4">工作流可视化</h2>
            <WorkflowVisualizer
              toolsConfig={toolsConfig}
              routingConfig={routingConfig}
            />
          </section>
        </div>
      </div>
    </div>
  );
}
