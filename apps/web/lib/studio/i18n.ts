'use client';

import { useTranslations } from 'next-intl';
import { useMemo } from 'react';

// i18next 兼容层:studio 迁入代码里有 ~50 处 t(key, {defaultValue}) 与
// t(key, '默认值') 调用(动态键的缺失回退场景),next-intl 没有 per-call
// default——在这里统一模拟,避免逐调用点清理。插值语法同时接受源码
// defaultValue 里的 {{var}}(i18next 旧形)与 {var}。
type TValues = Record<string, unknown>;
type TOptions = TValues & { defaultValue?: string };

function interpolate(template: string, values: TValues): string {
  return template.replace(/\{\{?\s*([\w.]+)\s*\}?\}/g, (match, key: string) => {
    const v = values[key];
    return v === undefined || v === null ? match : String(v);
  });
}

export type StudioT = {
  (key: string, arg?: TOptions | string): string;
  rich: ReturnType<typeof useTranslations>['rich'];
  has: (key: string) => boolean;
};

export function useStudioT(): StudioT {
  const t = useTranslations('studio');
  return useMemo(() => {
    const fn = ((key: string, arg?: TOptions | string) => {
      const opts: TOptions = typeof arg === 'string' ? { defaultValue: arg } : (arg ?? {});
      const { defaultValue, ...values } = opts;
      if (defaultValue !== undefined && !t.has(key)) {
        return interpolate(defaultValue, values);
      }
      return t(key, values as Record<string, string | number | Date>);
    }) as StudioT;
    fn.rich = t.rich.bind(t);
    fn.has = (key: string) => t.has(key);
    return fn;
  }, [t]);
}
