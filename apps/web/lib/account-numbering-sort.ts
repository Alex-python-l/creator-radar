const fallbackCollator = new Intl.Collator('en', {
  numeric: true,
  sensitivity: 'base',
});

interface NumberedSuffixLabel {
  prefix: string;
  numberText: string;
}

function parseNumberedSuffixLabel(label: string): NumberedSuffixLabel | null {
  const trimmed = label.trim();
  const match = trimmed.match(/^(.*?)(\d+)$/);
  if (!match) return null;
  return { prefix: match[1], numberText: match[2] };
}

function compareUnsignedIntegerText(a: string, b: string): number {
  const normalizedA = a.replace(/^0+/, '') || '0';
  const normalizedB = b.replace(/^0+/, '') || '0';

  if (normalizedA.length !== normalizedB.length) {
    return normalizedA.length - normalizedB.length;
  }

  if (normalizedA !== normalizedB) {
    return normalizedA < normalizedB ? -1 : 1;
  }

  return a.length - b.length;
}

export function compareAccountNumberingLabels(a: string, b: string): number {
  const trimmedA = a.trim();
  const trimmedB = b.trim();
  const parsedA = parseNumberedSuffixLabel(trimmedA);
  const parsedB = parseNumberedSuffixLabel(trimmedB);

  if (parsedA && parsedB) {
    const byPrefix = fallbackCollator.compare(parsedA.prefix, parsedB.prefix);
    if (byPrefix !== 0) return byPrefix;

    const byNumber = compareUnsignedIntegerText(parsedA.numberText, parsedB.numberText);
    if (byNumber !== 0) return byNumber;
  }

  const byLabel = fallbackCollator.compare(trimmedA, trimmedB);
  return byLabel !== 0 ? byLabel : trimmedA.localeCompare(trimmedB, 'en');
}
