'use client';

import type { Route } from 'next';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';

import { PRODUCTS, type ProductId } from '@svm/shared/products';

// 监控 | 运营 分段切换器(仅 admin 可见,由 (app)/layout 控制)。两个产品
// 已同壳同应用——next/link 客户端秒切,不再整页刷新。
export function ProductSwitcher() {
  const t = useTranslations('layout');
  const locale = useLocale();
  const pathname = usePathname();

  const current: ProductId =
    pathname === `/${locale}/studio` || pathname.startsWith(`/${locale}/studio/`)
      ? 'studio'
      : 'radar';
  const label: Record<ProductId, string> = { radar: t('monitor'), studio: t('ops') };
  const href = (path: string) =>
    (path === '/' ? `/${locale}/monitor` : `/${locale}${path}`) as Route;

  return (
    <div className="mb-5 flex rounded-lg border border-[color:var(--color-border)] bg-white/60 p-1 shadow-[0_8px_24px_rgba(68,48,126,0.04)] backdrop-blur-sm">
      {PRODUCTS.map((p) =>
        p.id === current ? (
          <span
            key={p.id}
            className="flex h-8 flex-1 items-center justify-center rounded-md bg-[color:var(--color-primary)] px-2 text-xs font-semibold text-[color:var(--color-primary-foreground)] shadow-[0_5px_12px_rgba(96,69,194,0.2)]"
          >
            {label[p.id]}
          </span>
        ) : (
          <Link
            key={p.id}
            href={href(p.path)}
            className="flex h-8 flex-1 items-center justify-center rounded-md px-2 text-xs font-semibold text-[color:var(--color-muted-foreground)] transition-colors hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)]"
          >
            {label[p.id]}
          </Link>
        ),
      )}
    </div>
  );
}
