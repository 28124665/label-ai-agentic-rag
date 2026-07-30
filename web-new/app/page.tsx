import { redirect } from 'next/navigation';

export default function Home() {
  // 重定向到对话页面
  redirect('/chat');
}
