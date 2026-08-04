import type {Metadata} from 'next';
import type {Route} from 'next';
import {redirect} from 'next/navigation';
import {getTranslations} from 'next-intl/server';

import {LoginForm} from '@/components/login-form';
import {getViewer} from '@/lib/auth/viewer';
import {env} from '@/lib/env';
import type {AppLocale} from '@/i18n/routing';

type Props = Readonly<{
  params: Promise<{locale: string}>;
  searchParams: Promise<{next?: string}>;
}>;

export async function generateMetadata({params}: Pick<Props, 'params'>): Promise<Metadata> {
  const {locale} = await params;
  const t = await getTranslations({locale, namespace: 'auth'});
  return {title: t('loginTitle')};
}

/** 只允许站内相对路径,防 open redirect(与 client 侧 safeNext 同规则)。 */
function safeNextPath(raw: string | undefined): string | null {
  if (!raw || !raw.startsWith('/')) return null;
  try {
    const u = new URL(raw, 'https://internal.invalid');
    if (u.origin !== 'https://internal.invalid') return null;
    return u.pathname + u.search + u.hash;
  } catch {
    return null;
  }
}

export default async function LoginPage({params, searchParams}: Props) {
  const [{locale}, {next}] = await Promise.all([params, searchParams]);
  const currentLocale = locale as AppLocale;

  // 已登录(查库为准)直接放行到目标页;middleware 对 /login 不做
  // cookie 判断,避免伪造 cookie 造成的重定向循环。
  const viewer = await getViewer();
  if (viewer) redirect((safeNextPath(next) ?? `/${currentLocale}`) as Route);

  // 整屏表单和品牌画布都在 LoginForm:登录/注册切换要联动鸢尾花构图,
  // 而 mode 是客户端状态,故视觉层一并放进客户端组件。
  return <LoginForm registerMode={env.REGISTER_MODE} />;
}
