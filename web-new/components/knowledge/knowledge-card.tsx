'use client';

import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { formatDate } from '@/lib/utils';
import { FileText, Calendar, Trash2 } from 'lucide-react';
import type { Dataset } from '@/services/knowledge.service';

interface KnowledgeCardProps {
  dataset: Dataset;
  onEdit?: (dataset: Dataset) => void;
  onDelete?: (dataset: Dataset) => void;
}

export function KnowledgeCard({ dataset, onEdit, onDelete }: KnowledgeCardProps) {
  const handleClick = () => {
    window.location.href = `/knowledge/${dataset.id}`;
  };

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete?.(dataset);
  };

  return (
    <Card
      className="cursor-pointer hover:shadow-lg transition-shadow"
      onClick={handleClick}
    >
      <CardHeader>
        <CardTitle className="text-lg line-clamp-1">{dataset.name}</CardTitle>
        <CardDescription className="line-clamp-2 min-h-[2.5rem]">
          {dataset.description || '暂无描述'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-center text-sm text-gray-600">
          <FileText className="h-4 w-4 mr-2" />
          <span>{dataset.document_count} 个文档</span>
          <span className="mx-2">·</span>
          <span>{dataset.chunk_count} 个片段</span>
        </div>
        <div className="flex items-center text-sm text-gray-500">
          <Calendar className="h-4 w-4 mr-2" />
          <span>创建于 {formatDate(dataset.created_at)}</span>
        </div>
      </CardContent>
      <CardFooter className="flex justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={handleDelete}
          className="text-red-600 hover:text-red-700 hover:bg-red-50"
        >
          <Trash2 className="h-4 w-4 mr-1" />
          删除
        </Button>
      </CardFooter>
    </Card>
  );
}
