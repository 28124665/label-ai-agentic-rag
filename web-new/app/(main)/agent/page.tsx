'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AgentCard } from '@/components/agent/agent-card';
import { agentService, Agent } from '@/services/agent.service';

export default function AgentPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadAgents();
  }, []);

  const loadAgents = async () => {
    try {
      const data = await agentService.list(1, 50);
      setAgents(data.items);
    } catch (error) {
      console.error('Failed to load agents:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAgent = async () => {
    try {
      const agent = await agentService.create({
        name: '新 Agent',
        description: '请配置 Agent 描述',
      });
      router.push(`/agent/${agent.id}`);
    } catch (error) {
      console.error('Failed to create agent:', error);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-gray-400">加载中...</div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Agent 管理</h1>
          <p className="text-sm text-gray-500 mt-1">
            配置和管理智能 Agent，定义工具、路由和降级策略
          </p>
        </div>
        <Button onClick={handleCreateAgent}>
          <Plus className="h-4 w-4 mr-2" />
          创建 Agent
        </Button>
      </div>

      {agents.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400">
          <div className="text-6xl mb-4">🤖</div>
          <div className="text-lg font-medium mb-2">暂无 Agent</div>
          <div className="text-sm mb-4">点击创建按钮开始配置您的第一个 Agent</div>
          <Button onClick={handleCreateAgent}>
            <Plus className="h-4 w-4 mr-2" />
            创建 Agent
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {agents.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      )}
    </div>
  );
}
