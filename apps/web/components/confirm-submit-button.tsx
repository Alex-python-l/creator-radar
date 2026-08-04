'use client';

import { useRef } from 'react';
import { useTranslations } from 'next-intl';

import { Button } from '@/components/ui';

type ButtonProps = React.ComponentProps<typeof Button>;

export function ConfirmSubmitButton({
  confirmMessage,
  confirmTitle,
  confirmLabel,
  destructive = false,
  ...rest
}: Omit<ButtonProps, 'type' | 'onClick'> & {
  confirmMessage: string;
  confirmTitle?: string;
  confirmLabel?: string;
  destructive?: boolean;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const common = useTranslations('common');

  const open = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    formRef.current = e.currentTarget.form;
    dialogRef.current?.showModal();
  };
  const close = () => dialogRef.current?.close();
  const confirm = () => {
    close();
    formRef.current?.requestSubmit();
  };

  return (
    <>
      <Button type="submit" {...rest} onClick={open} />
      <dialog
        ref={dialogRef}
        className="m-auto rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-0 backdrop:bg-black/40 max-w-md w-full"
      >
        <div className="p-6 space-y-4">
          <div>
            <h2 className="text-base font-semibold">{confirmTitle ?? common('confirmTitle')}</h2>
            <p className="mt-2 text-sm text-[color:var(--color-muted-foreground)]">
              {confirmMessage}
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={close}>
              {common('cancel')}
            </Button>
            <Button
              type="button"
              variant={destructive ? 'danger' : 'primary'}
              size="sm"
              onClick={confirm}
            >
              {confirmLabel ?? common('confirm')}
            </Button>
          </div>
        </div>
      </dialog>
    </>
  );
}
