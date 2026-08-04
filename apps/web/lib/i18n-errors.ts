type Translate = (key: string, values?: Record<string, number | string>) => string;

export function translateActionError(message: string, t: Translate): string {
  if (message === 'invalid group id') return t('invalidGroupId');
  if (message === 'target group not found') return t('targetGroupNotFound');
  if (message === 'group not found') return t('groupNotFound');
  if (message === '组名不能为空') return t('groupNameRequired');
  if (message === '组名最多 40 字符') return t('groupNameTooLong');
  if (message === '组名无效') return t('invalidGroupName');
  if (message === '已存在同名组') return t('duplicateGroupName');
  if (message === '系统组「未分组」不可删除') return t('cannotDeleteDefaultGroup');
  if (message === '至少选 1 个账号') return t('selectAtLeastOneAccount');
  if (message === 'alias too long') return t('aliasTooLong');
  if (message === 'alias duplicate') return t('aliasDuplicate');

  const notEmpty = message.match(/^组里还有 (\d+) 个账号，先移走$/);
  if (notEmpty) return t('groupNotEmpty', {count: Number(notEmpty[1])});

  return message;
}
