import Image from 'next/image';

import irisCluster from '@/assets/botanical/iris-cluster.webp';
import irisMeadow from '@/assets/botanical/iris-meadow.webp';
import irisPetals from '@/assets/botanical/iris-petals.webp';
import irisSprig from '@/assets/botanical/iris-sprig.webp';
import {cn} from '@/lib/utils';

const botanicalAssets = {
  cluster: irisCluster,
  meadow: irisMeadow,
  petals: irisPetals,
  sprig: irisSprig,
} as const;

export type BotanicalAccentVariant = keyof typeof botanicalAssets;
export type BotanicalAccentMotion = 'drift' | 'sway';

type Props = Readonly<{
  variant: BotanicalAccentVariant;
  className?: string;
  sizes?: string;
  priority?: boolean;
  mirrored?: boolean;
  motion?: BotanicalAccentMotion;
}>;

/**
 * 统一的装饰植物层。素材本身有透明通道；调用方只负责摆位和透明度。
 * 所有实例都不进入可访问性树，也不会拦截页面交互。
 */
export function BotanicalAccent({
  variant,
  className,
  sizes,
  priority = false,
  mirrored = false,
  motion,
}: Props) {
  return (
    <Image
      src={botanicalAssets[variant]}
      alt=""
      aria-hidden="true"
      priority={priority}
      sizes={sizes}
      className={cn(
        'pointer-events-none select-none object-contain',
        mirrored && '-scale-x-100',
        motion === 'drift' && 'botanical-drift',
        motion === 'sway' && 'botanical-sway',
        className,
      )}
    />
  );
}
