'use client';

import { useState, KeyboardEvent, useRef } from 'react';
import { Send, Paperclip, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

interface MessageInputProps {
  onSend: (content: string, files: File[]) => void;
  disabled?: boolean;
}

const ALLOWED_FILE_TYPES = '.pdf,.txt,.doc,.docx,.md,.csv,.xlsx,.png,.jpg,.jpeg';

export function MessageInput({ onSend, disabled }: MessageInputProps) {
  const [content, setContent] = useState('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSend = () => {
    if ((content.trim() || selectedFiles.length > 0) && !disabled) {
      onSend(content, selectedFiles);
      setContent('');
      setSelectedFiles([]);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    setSelectedFiles(prev => [...prev, ...files]);
  };

  const handleRemoveFile = (index: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleFileButtonClick = () => {
    fileInputRef.current?.click();
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  return (
    <div className="max-w-3xl mx-auto">
      {/* File list */}
      {selectedFiles.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {selectedFiles.map((file, index) => (
            <div
              key={index}
              className="flex items-center gap-1.5 bg-gray-100 rounded-md px-2 py-1 text-xs text-gray-700"
            >
              <span className="max-w-[150px] truncate">{file.name}</span>
              <span className="text-gray-400">{formatFileSize(file.size)}</span>
              <button
                onClick={() => handleRemoveFile(index)}
                className="text-gray-400 hover:text-gray-600"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="relative">
        <Textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息... (Shift+Enter 换行)"
          disabled={disabled}
          className="min-h-[80px] resize-none pr-20"
        />
        <div className="absolute right-2 bottom-2 flex items-center gap-1">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ALLOWED_FILE_TYPES}
            onChange={handleFileSelect}
            className="hidden"
          />
          <Button
            onClick={handleFileButtonClick}
            disabled={disabled}
            size="icon"
            variant="ghost"
            className="h-8 w-8 text-gray-500 hover:text-gray-700"
          >
            <Paperclip className="h-4 w-4" />
          </Button>
          <Button
            onClick={handleSend}
            disabled={(!content.trim() && selectedFiles.length === 0) || disabled}
            size="icon"
            className="h-8 w-8"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div className="text-xs text-gray-400 mt-2 text-center">
        AI 可能会犯错，请核实重要信息
      </div>
    </div>
  );
}
