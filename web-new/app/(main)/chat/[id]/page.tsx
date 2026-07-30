'use client';

import { useEffect, useState, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { MessageList } from '@/components/chat/message-list';
import { MessageInput } from '@/components/chat/message-input';
import { conversationService, Conversation, Message, SSEEvent } from '@/services/conversation.service';

export default function ChatDetailPage() {
  const params = useParams();
  const router = useRouter();
  const conversationId = params.id as string;
  
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadConversation();
    loadMessages();
  }, [conversationId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const loadConversation = async () => {
    try {
      const data = await conversationService.get(conversationId);
      setConversation(data);
    } catch (error) {
      console.error('Failed to load conversation:', error);
      router.push('/chat');
    }
  };

  const loadMessages = async () => {
    try {
      const data = await conversationService.getMessages(conversationId);
      setMessages(data);
    } catch (error) {
      console.error('Failed to load messages:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSendMessage = async (content: string, files: File[]) => {
    if ((!content.trim() && files.length === 0) || sending) return;

    setSending(true);

    // Add user message immediately
    const userMessage: Message = {
      id: `temp-${Date.now()}`,
      conversation_id: conversationId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMessage]);

    // Create assistant message placeholder
    const assistantMessage: Message = {
      id: `temp-assistant-${Date.now()}`,
      conversation_id: conversationId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, assistantMessage]);

    try {
      await conversationService.sendMessage(
        conversationId,
        content,
        (event: SSEEvent) => {
          if (event.type === 'text' && event.content) {
            setMessages(prev => {
              const updated = [...prev];
              const lastMsg = updated[updated.length - 1];
              if (lastMsg.role === 'assistant') {
                lastMsg.content += event.content;
              }
              return updated;
            });
          } else if (event.type === 'done') {
            setSending(false);
          } else if (event.type === 'error') {
            console.error('SSE error:', event.error);
            setSending(false);
          }
        },
        (error) => {
          console.error('Send message error:', error);
          setSending(false);
        },
        files
      );
    } catch (error) {
      console.error('Failed to send message:', error);
      setSending(false);
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
    <div className="flex flex-col h-[calc(100vh-3.5rem)]">
      {/* Header */}
      <div className="h-14 border-b bg-white flex items-center px-4 gap-4">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => router.push('/chat')}
        >
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <div className="font-medium">{conversation?.title || '对话'}</div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-auto bg-gray-50">
        <MessageList messages={messages} />
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t bg-white p-4">
        <MessageInput
          onSend={handleSendMessage}
          disabled={sending}
        />
      </div>
    </div>
  );
}
