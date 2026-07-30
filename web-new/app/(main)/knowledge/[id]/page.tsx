'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, Upload, FileText, Calendar, Database, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DocumentList } from '@/components/knowledge/document-list';
import { UploadDialog } from '@/components/knowledge/upload-dialog';
import { SearchTest } from '@/components/knowledge/search-test';
import { knowledgeService, type Dataset, type Document } from '@/services/knowledge.service';
import { formatDate } from '@/lib/utils';

export default function KnowledgeDetailPage() {
  const params = useParams();
  const router = useRouter();
  const datasetId = params.id as string;

  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'documents' | 'search'>('documents');

  useEffect(() => {
    if (datasetId) {
      loadDataset();
      loadDocuments();
    }
  }, [datasetId]);

  const loadDataset = async () => {
    try {
      const data = await knowledgeService.getDataset(datasetId);
      setDataset(data);
    } catch (error) {
      console.error('加载知识库详情失败:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadDocuments = async () => {
    try {
      setDocumentsLoading(true);
      const data = await knowledgeService.listDocuments(datasetId, 1, 100);
      setDocuments(data.items);
    } catch (error) {
      console.error('加载文档列表失败:', error);
    } finally {
      setDocumentsLoading(false);
    }
  };

  const handleDeleteDocument = async (doc: Document) => {
    try {
      await knowledgeService.deleteDocument(datasetId, doc.id);
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
      // 重新加载知识库信息以更新统计
      loadDataset();
    } catch (error) {
      console.error('删除文档失败:', error);
      throw error;
    }
  };

  const handleUploadSuccess = () => {
    loadDocuments();
    loadDataset();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-3.5rem)]">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
        <span className="ml-2 text-gray-500">加载中...</span>
      </div>
    );
  }

  if (!dataset) {
    return (
      <div className="flex flex-col items-center justify-center h-[calc(100vh-3.5rem)] text-gray-400">
        <div className="text-6xl mb-4">😕</div>
        <div className="text-lg font-medium mb-2">知识库不存在</div>
        <Button onClick={() => router.push('/knowledge')}>返回知识库列表</Button>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* 返回按钮 */}
      <Button
        variant="ghost"
        onClick={() => router.push('/knowledge')}
        className="mb-2"
      >
        <ArrowLeft className="h-4 w-4 mr-2" />
        返回
      </Button>

      {/* 知识库基本信息 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl">{dataset.name}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {dataset.description && (
            <div className="text-gray-600">{dataset.description}</div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-4 border-t">
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5 text-blue-500" />
              <div>
                <div className="text-sm text-gray-500">文档数量</div>
                <div className="text-lg font-semibold">{dataset.document_count}</div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <FileText className="h-5 w-5 text-green-500" />
              <div>
                <div className="text-sm text-gray-500">片段数量</div>
                <div className="text-lg font-semibold">{dataset.chunk_count}</div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Calendar className="h-5 w-5 text-purple-500" />
              <div>
                <div className="text-sm text-gray-500">创建时间</div>
                <div className="text-sm font-medium">{formatDate(dataset.created_at)}</div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5 text-orange-500" />
              <div>
                <div className="text-sm text-gray-500">Embedding 模型</div>
                <div className="text-sm font-medium truncate">{dataset.embedding_model}</div>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tab 切换 */}
      <div className="border-b">
        <div className="flex gap-4">
          <button
            onClick={() => setActiveTab('documents')}
            className={`pb-2 px-1 border-b-2 transition-colors ${
              activeTab === 'documents'
                ? 'border-primary text-primary font-medium'
                : 'border-transparent text-gray-600 hover:text-gray-900'
            }`}
          >
            文档列表
          </button>
          <button
            onClick={() => setActiveTab('search')}
            className={`pb-2 px-1 border-b-2 transition-colors ${
              activeTab === 'search'
                ? 'border-primary text-primary font-medium'
                : 'border-transparent text-gray-600 hover:text-gray-900'
            }`}
          >
            检索测试
          </button>
        </div>
      </div>

      {/* 内容区域 */}
      {activeTab === 'documents' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="text-sm text-gray-600">
              共 {documents.length} 个文档
            </div>
            <Button onClick={() => setUploadDialogOpen(true)}>
              <Upload className="h-4 w-4 mr-2" />
              上传文档
            </Button>
          </div>
          <DocumentList
            documents={documents}
            loading={documentsLoading}
            onDelete={handleDeleteDocument}
          />
        </div>
      )}

      {activeTab === 'search' && (
        <Card>
          <CardContent className="pt-6">
            <SearchTest datasetId={datasetId} />
          </CardContent>
        </Card>
      )}

      {/* 上传弹窗 */}
      <UploadDialog
        open={uploadDialogOpen}
        onOpenChange={setUploadDialogOpen}
        datasetId={datasetId}
        onSuccess={handleUploadSuccess}
      />
    </div>
  );
}
