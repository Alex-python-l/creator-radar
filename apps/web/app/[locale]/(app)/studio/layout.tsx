import { NextIntlClientProvider } from 'next-intl';
import { getMessages } from 'next-intl/server';

import { StudioProviders } from '@/components/studio/studio-providers';
import { requireViewer } from '@/lib/auth/viewer';

type Props = Readonly<{
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}>;

// Studio 与 Radar 使用同一个服务端工作空间上下文；登录门在上层，
// FastAPI 仍会对每个请求重新解析并校验当前成员关系。
export default async function StudioLayout({ children, params }: Props) {
  await requireViewer();
  const { locale } = await params;

  // 外层 [locale]/layout 的 provider 只带 studio.shell 子集(公众页减重);
  // 运营段在这里嵌套全量 messages,studio 页面的 useStudioT 才拿得到词条。
  const messages = await getMessages({ locale });

  return (
    <div data-studio className="min-w-0">
      <NextIntlClientProvider messages={messages}>
        <StudioProviders>{children}</StudioProviders>
      </NextIntlClientProvider>
    </div>
  );
}
