'use client';

import { useState, useRef } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Upload, X, FileText, Loader2 } from 'lucide-react';
import { knowledgeService } from '@/services/knowledge.service';

interface UploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  datasetId: string;
  onSuccess?: () => void;
}

export function UploadDialog({ open, onOpenChange, datasetId, onSuccess }: UploadDialogProps) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ name: string; progress: number; status: 'pending' | 'uploading' | 'success' | 'error' }[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    setSelectedFiles((prev) => [...prev, ...files]);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleRemoveFile = (index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) return;

    setUploading(true);
    const progress = selectedFiles.map((f) => ({
      name: f.name,
      progress: 0,
      status: 'pending' as const,
    }));
    setUploadProgress(progress);

    for (let i = 0; i < selectedFiles.length; i++) {
      const file = selectedFiles[i];
      setUploadProgress((prev) =>
        prev.map((p, idx) =>
          idx === i ? { ...p, status: 'uploading', progress: 50 } : p
        )
      );

      try {
        await knowledgeService.uploadDocument(datasetId, file);
        setUploadProgress((prev) =>
          prev.map((p, idx) =>
            idx === i ? { ...p, status: 'success', progress: 100 } : p
          )
        );
      } catch (error) {
        setUploadProgress((prev) =>
          prev.map((p, idx) =>
            idx === i ? { ...p, status: 'error', progress: 0 } : p
          )
        );
      }
    }

    setUploading(false);
    onSuccess?.();

    // 延迟关闭，让用户看到结果
    setTimeout(() => {
      handleClose();
    }, 1500);
  };

  const handleClose = () => {
    if (uploading) return;
    setSelectedFiles([]);
    setUploadProgress([]);
    onOpenChange(false);
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>上传文档</DialogTitle>
          <DialogDescription>
            选择要上传到知识库的文档文件
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 文件选择区域 */}
          {!uploading && uploadProgress.length === 0 && (
            <div
              onClick={() => fileInputRef.current?.click()}
              className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center cursor-pointer hover:border-primary hover:bg-primary/5 transition-colors"
            >
              <Upload className="h-10 w-10 mx-auto mb-3 text-gray-400" />
              <div className="text-sm font-medium mb-1">点击选择文件</div>
              <div className="text-xs text-gray-500">
                支持 PDF、TXT、DOCX、MD 等格式
              </div>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.txt,.doc,.docx,.md,.csv,.xlsx"
                onChange={handleFileSelect}
                className="hidden"
              />
            </div>
          )}

          {/* 已选文件列表 */}
          {!uploading && uploadProgress.length === 0 && selectedFiles.length > 0 && (
            <div className="space-y-2">
              <div className="text-sm font-medium">已选择 {selectedFiles.length} 个文件</div>
              <div className="max-h-[200px] overflow-y-auto space-y-1">
                {selectedFiles.map((file, index) => (
                  <div
                    key={index}
                    className="flex items-center justify-between p-2 bg-gray-50 rounded"
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <FileText className="h-4 w-4 text-blue-500 flex-shrink-0" />
                      <span className="text-sm truncate">{file.name}</span>
                      <span className="text-xs text-gray-500 flex-shrink-0">
                        {formatFileSize(file.size)}
                      </span>
                    </div>
                    <button
                      onClick={() => handleRemoveFile(index)}
                      className="text-gray-400 hover:text-red-500 ml-2"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 上传进度 */}
          {uploadProgress.length > 0 && (
            <div className="space-y-2">
              <div className="text-sm font-medium">上传进度</div>
              <div className="max-h-[200px] overflow-y-auto space-y-1">
                {uploadProgress.map((item, index) => (
                  <div key={index} className="p-2 bg-gray-50 rounded">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm truncate flex-1">{item.name}</span>
                      {item.status === 'success' && (
                        <span className="text-xs text-green-600 ml-2">✓ 成功</span>
                      )}
                      {item.status === 'error' && (
                        <span className="text-xs text-red-600 ml-2">✗ 失败</span>
                      )}
                      {item.status === 'uploading' && (
                        <Loader2 className="h-3 w-3 animate-spin ml-2" />
                      )}
                    </div>
                    <div className="h-1 bg-gray-200 rounded-full overflow-hidden">
                      <div
                        className={`h-full transition-all ${
                          item.status === 'success'
                            ? 'bg-green-500'
                            : item.status === 'error'
                            ? 'bg-red-500'
                            : 'bg-blue-500'
                        }`}
                        style={{ width: `${item.progress}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          {!uploading && (
            <>
              <Button variant="outline" onClick={handleClose}>
                取消
              </Button>
              <Button onClick={handleUpload} disabled={selectedFiles.length === 0}>
                上传 {selectedFiles.length > 0 && `(${selectedFiles.length})`}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
