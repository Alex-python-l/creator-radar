'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

import { AUTH_EXPIRED_EVENT } from '@/lib/studio/api';
import { Toast } from '@/components/ui';

// 原 App.tsx 的 Toast 状态(onToast prop 下钻)与 401 全局兜底,改为
// context + 顶层监听,挂在 studio 段 layout 上。
export type ToastState = {
  msg: string;
  tone?: 'blue' | 'green' | 'red' | 'orange' | 'gray';
} | null;

const ToastCtx = createContext<(t: ToastState) => void>(() => {});

export function useStudioToast() {
  return useContext(ToastCtx);
}

export function StudioProviders({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastState>(null);

  // 任意请求 401(会话过期)→ 整页去登录,带回跳。轮询页面不再对着
  // 过期数据空转。
  useEffect(() => {
    const onExpired = () => {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.assign(`/login?next=${next}`);
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  return (
    <ToastCtx.Provider value={setToast}>
      {children}
      {toast ? (
        <Toast message={toast.msg} tone={toast.tone ?? 'blue'} onDismiss={() => setToast(null)} />
      ) : null}
    </ToastCtx.Provider>
  );
}
