'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { formatDate } from '@/lib/utils';
import { FileText, Trash2, Loader2, CheckCircle2, AlertCircle, Clock } from 'lucide-react';
import type { Document } from '@/services/knowledge.service';

interface DocumentListProps {
  documents: Document[];
  loading?: boolean;
  onDelete?: (document: Document) => void;
}

const statusConfig = {
  uploading: { label: '上传中', icon: Loader2, color: 'text-blue-600', spin: true },
  parsing: { label: '解析中', icon: Clock, color: 'text-yellow-600', spin: false },
  completed: { label: '已完成', icon: CheckCircle2, color: 'text-green-600', spin: false },
  failed: { label: '失败', icon: AlertCircle, color: 'text-red-600', spin: false },
};

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function DocumentList({ documents, loading = false, onDelete }: DocumentListProps) {
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const handleDelete = async (doc: Document) => {
    if (!confirm(`确定要删除文档 "${doc.name}" 吗？`)) return;
    setDeletingId(doc.id);
    try {
      await onDelete?.(doc);
    } finally {
      setDeletingId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
        <span className="ml-2 text-gray-500">加载中...</span>
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-gray-400">
        <FileText className="h-12 w-12 mb-3" />
        <div className="text-sm">暂无文档</div>
        <div className="text-xs mt-1">点击上方"上传文档"按钮添加文档</div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {documents.map((doc) => {
        const status = statusConfig[doc.status];
        const StatusIcon = status.icon;
        const isDeleting = deletingId === doc.id;

        return (
          <div
            key={doc.id}
            className="flex items-center justify-between p-4 border rounded-lg hover:bg-gray-50 transition-colors"
          >
            <div className="flex items-center gap-3 min-w-0 flex-1">
              <FileText className="h-8 w-8 text-blue-500 flex-shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm truncate">{doc.name}</div>
                <div className="flex items-center gap-3 text-xs text-gray-500 mt-1">
                  <span>{formatFileSize(doc.size)}</span>
                  <span>·</span>
                  <span>{doc.chunk_count} 个片段</span>
                  <span>·</span>
                  <span>{formatDate(doc.created_at)}</span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3 ml-4">
              <div className={`flex items-center gap-1 text-xs ${status.color}`}>
                <StatusIcon className={`h-3.5 w-3.5 ${status.spin ? 'animate-spin' : ''}`} />
                <span>{status.label}</span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleDelete(doc)}
                disabled={isDeleting}
                className="text-red-600 hover:text-red-700 hover:bg-red-50"
              >
                {isDeleting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4" />
                )}
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
