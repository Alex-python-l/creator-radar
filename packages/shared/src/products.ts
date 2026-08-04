export type ProductId = 'radar' | 'studio';

export type Product = { id: ProductId; path: string };

// 前端合并(2026-07-17)后两个产品同属一个 Next 应用。path 是 locale 无关
// 的路径段,消费方(apps/web ProductSwitcher)自行拼 /{locale} 前缀。
export const PRODUCTS: readonly Product[] = [
  { id: 'radar', path: '/monitor' },
  { id: 'studio', path: '/studio' },
] as const;
