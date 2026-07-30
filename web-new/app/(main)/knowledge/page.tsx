'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Plus, Search, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { KnowledgeCard } from '@/components/knowledge/knowledge-card';
import { knowledgeService, type Dataset } from '@/services/knowledge.service';

export default function KnowledgePage() {
  const router = useRouter();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [newDataset, setNewDataset] = useState({ name: '', description: '' });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    loadDatasets();
  }, []);

  const loadDatasets = async () => {
    try {
      const data = await knowledgeService.listDatasets(1, 50);
      setDatasets(data.items);
    } catch (error) {
      console.error('加载知识库列表失败:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!newDataset.name.trim()) return;

    setCreating(true);
    try {
      const dataset = await knowledgeService.createDataset({
        name: newDataset.name,
        description: newDataset.description || undefined,
      });
      setDatasets((prev) => [dataset, ...prev]);
      setCreateDialogOpen(false);
      setNewDataset({ name: '', description: '' });
      router.push(`/knowledge/${dataset.id}`);
    } catch (error) {
      console.error('创建知识库失败:', error);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (dataset: Dataset) => {
    if (!confirm(`确定要删除知识库 "${dataset.name}" 吗？此操作不可恢复。`)) return;

    try {
      await knowledgeService.deleteDataset(dataset.id);
      setDatasets((prev) => prev.filter((d) => d.id !== dataset.id));
    } catch (error) {
      console.error('删除知识库失败:', error);
    }
  };

  const filteredDatasets = datasets.filter(
    (d) =>
      d.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      d.description?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="p-6 space-y-6">
      {/* 页面标题和操作栏 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">知识库</h1>
          <p className="text-gray-500 text-sm mt-1">管理您的知识库，上传文档进行检索</p>
        </div>
        <Button onClick={() => setCreateDialogOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          创建知识库
        </Button>
      </div>

      {/* 搜索框 */}
      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
        <Input
          placeholder="搜索知识库..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="pl-10"
        />
      </div>

      {/* 知识库列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
          <span className="ml-2 text-gray-500">加载中...</span>
        </div>
      ) : filteredDatasets.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-gray-400">
          <div className="text-6xl mb-4">📚</div>
          <div className="text-lg font-medium mb-2">
            {searchQuery ? '未找到匹配的知识库' : '暂无知识库'}
          </div>
          <div className="text-sm">
            {searchQuery ? '尝试使用不同的搜索词' : '点击上方"创建知识库"按钮开始'}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filteredDatasets.map((dataset) => (
            <KnowledgeCard
              key={dataset.id}
              dataset={dataset}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      {/* 创建知识库弹窗 */}
      <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>创建知识库</DialogTitle>
            <DialogDescription>
              创建一个新的知识库来存储和检索文档
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>名称</Label>
              <Input
                value={newDataset.name}
                onChange={(e) => setNewDataset((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="输入知识库名称"
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label>描述（可选）</Label>
              <Textarea
                value={newDataset.description}
                onChange={(e) => setNewDataset((prev) => ({ ...prev, description: e.target.value }))}
                placeholder="输入知识库描述"
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={handleCreate} disabled={creating || !newDataset.name.trim()}>
              {creating && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
