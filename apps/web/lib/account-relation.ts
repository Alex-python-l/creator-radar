/**
 * 监控订阅的层级：'own'=自己的账号，'competitor'=竞品账号。
 * client-safe（无服务端依赖），actions / queries / api / 组件统一从这里引用。
 */
export const ACCOUNT_RELATIONS = ['own', 'competitor'] as const;

export type AccountRelation = (typeof ACCOUNT_RELATIONS)[number];

export function isAccountRelation(value: unknown): value is AccountRelation {
  return (ACCOUNT_RELATIONS as readonly unknown[]).includes(value);
}
