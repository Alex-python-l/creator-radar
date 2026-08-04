import {BotanicalAccent} from '@/components/botanical-accent';
import Ops from '@/components/studio/pages/Ops';

export default function Page() {
  return (
    <div className="relative mx-auto w-full max-w-[1600px] px-5 py-7 lg:px-8 lg:py-8">
      <BotanicalAccent
        variant="sprig"
        sizes="380px"
        mirrored
        className="absolute -right-24 -top-20 w-[380px] rotate-[9deg] opacity-[0.14]"
      />
      <BotanicalAccent
        variant="petals"
        sizes="220px"
        motion="drift"
        className="absolute left-[34%] -top-20 w-[220px] opacity-[0.12]"
      />
      <div className="relative z-10">
        <Ops />
      </div>
    </div>
  );
}
