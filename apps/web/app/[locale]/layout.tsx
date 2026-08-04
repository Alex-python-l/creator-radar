import type {Metadata} from 'next';
import {notFound} from 'next/navigation';
import {hasLocale, NextIntlClientProvider, type AbstractIntlMessages} from 'next-intl';
import {getMessages, getTranslations} from 'next-intl/server';

import {routing, type AppLocale} from '@/i18n/routing';
import {Providers} from '@/app/providers';

import '../globals.css';

type Props = Readonly<{
  children: React.ReactNode;
  params: Promise<{locale: string}>;
}>;

export async function generateMetadata({params}: Pick<Props, 'params'>): Promise<Metadata> {
  const {locale} = await params;
  const t = await getTranslations({locale, namespace: 'metadata'});

  return {
    title: t('title'),
    description: t('description'),
  };
}

// 纯壳:html/body + providers。侧栏与登录门在 (app)/layout.tsx —
// /login 也在本壳下,但不套侧栏、不要求会话。
export default async function LocaleLayout({children, params}: Props) {
  const {locale} = await params;
  if (!hasLocale(routing.locales, locale)) notFound();

  const currentLocale = locale as AppLocale;

  // 公众页不下发 studio(运营)词条(~40KB/语言):只保留统一侧栏导航要用的
  // studio.shell 子集;studio 段内由其 layout 的嵌套 provider 供给全量。
  const messages = await getMessages({locale: currentLocale});
  const {studio, ...shared} = messages as Record<string, AbstractIntlMessages>;
  const clientMessages: AbstractIntlMessages = {
    ...shared,
    studio: {shell: (studio as Record<string, AbstractIntlMessages>)?.shell},
  };

  return (
    <html lang={currentLocale === 'zh' ? 'zh-CN' : 'en'}>
      <body className="min-h-screen antialiased">
        <Providers>
          <NextIntlClientProvider messages={clientMessages}>{children}</NextIntlClientProvider>
        </Providers>
      </body>
    </html>
  );
}
