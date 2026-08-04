'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useRef } from 'react';

/**
 * 焦点感知 + 任务感知的自动刷新:
 * - tab 可见时启用,不可见时停;tab 切回 / window focus / network online 立刻 tick
 * - 每次 tick 先 ping /api/job-stats 拿 active(queued+running)
 *   - active > 0:触发 router.refresh + invalidate accounts,下次 5s(activeIntervalMs)
 *   - active = 0:跳过 refresh(节省 RSC + DB),下次 60s(idleIntervalMs)
 *   - active 状态由 0→>0 边沿:立刻补一次 refresh,确保用户能尽快看到新任务进度
 *
 * 1s 防抖避免 visibility + focus 同时触发重复请求。
 */
export function AutoRefresh({
  activeIntervalMs = 5000,
  idleIntervalMs = 60_000,
}: {
  activeIntervalMs?: number;
  idleIntervalMs?: number;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastRefreshAt = useRef(0);
  const lastActive = useRef(0);
  const tickingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const refresh = () => {
      const now = Date.now();
      if (now - lastRefreshAt.current < 1000) return;
      lastRefreshAt.current = now;
      router.refresh();
      queryClient.invalidateQueries({ queryKey: ['accounts'] });
    };

    const scheduleNext = (ms: number) => {
      if (cancelled) return;
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(tick, ms);
    };

    const clearTimer = () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const tick = async () => {
      if (cancelled || document.hidden) return;
      if (tickingRef.current) return;
      tickingRef.current = true;
      try {
        let active = 0;
        try {
          const res = await fetch('/api/job-stats', { cache: 'no-store' });
          if (res.ok) {
            const body = (await res.json()) as { active?: number };
            active = Number(body.active ?? 0);
          }
        } catch {
          // 网络瞬断:按 idle 处理,下个 tick 再试
        }

        if (cancelled || document.hidden) return;

        const edge = lastActive.current === 0 && active > 0;
        lastActive.current = active;

        if (active > 0 || edge) refresh();
        scheduleNext(active > 0 ? activeIntervalMs : idleIntervalMs);
      } finally {
        tickingRef.current = false;
      }
    };

    const kick = () => {
      if (document.hidden) return;
      clearTimer();
      void tick();
    };

    const onVisibilityChange = () => {
      if (document.hidden) clearTimer();
      else kick();
    };

    if (!document.hidden) kick();

    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('focus', kick);
    window.addEventListener('online', kick);

    return () => {
      cancelled = true;
      clearTimer();
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('focus', kick);
      window.removeEventListener('online', kick);
    };
  }, [router, queryClient, activeIntervalMs, idleIntervalMs]);

  return null;
}
