'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ConversationList } from '@/components/chat/conversation-list';
import { conversationService, Conversation } from '@/services/conversation.service';

export default function ChatPage() {
  const router = useRouter();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadConversations();
  }, []);

  const loadConversations = async () => {
    try {
      const data = await conversationService.list(1, 50);
      setConversations(data.items);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateConversation = async () => {
    try {
      const conversation = await conversationService.create();
      router.push(`/chat/${conversation.id}`);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      {/* Left sidebar - Conversation list */}
      <div className="w-80 border-r bg-white flex flex-col">
        <div className="p-4 border-b">
          <Button onClick={handleCreateConversation} className="w-full">
            <Plus className="h-4 w-4 mr-2" />
            新建对话
          </Button>
        </div>
        <div className="flex-1 overflow-auto">
          <ConversationList
            conversations={conversations}
            loading={loading}
          />
        </div>
      </div>

      {/* Right side - Chat area */}
      <div className="flex-1 flex items-center justify-center bg-gray-50">
        <div className="text-center text-gray-400">
          <div className="text-6xl mb-4">💬</div>
          <div className="text-lg font-medium mb-2">开始新的对话</div>
          <div className="text-sm">选择一个对话或创建新对话开始聊天</div>
        </div>
      </div>
    </div>
  );
}
