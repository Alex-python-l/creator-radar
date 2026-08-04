import assert from 'node:assert/strict';
import test from 'node:test';

const { compareAccountNumberingLabels } = await import('../lib/account-numbering-sort.ts');

const orderedGenericLabels = [
  'AA2',
  'AA10',
  'AU2',
  'AU10',
  'CA_1',
  'CA_2',
  'CA_10',
  'O1',
  'O9',
  'O10',
  'O11',
  'PH3',
  'PH10',
  'R9',
  'R32',
  'UAE1',
  'UAE3',
  'XX_-01',
  'XX_-09',
  'XX_-10',
  'ZZ2',
  'ZZ10',
];

function deterministicShuffle(labels) {
  const length = labels.length;
  return labels
    .map((label, index) => ({ label, rank: (index * 37 + 11) % length }))
    .sort((a, b) => a.rank - b.rank)
    .map((item) => item.label);
}

test('account numbering labels sort any text-prefix numeric suffix naturally', () => {
  const shuffled = deterministicShuffle(orderedGenericLabels);

  assert.notDeepEqual(shuffled, orderedGenericLabels);
  assert.deepEqual(
    [...shuffled].sort(compareAccountNumberingLabels),
    orderedGenericLabels,
  );
});
