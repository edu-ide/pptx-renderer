import { describe, expect, it } from 'vitest';
import { parseXml } from '../../../src/parser/XmlParser';

describe('SafeXmlNode.attr', () => {
  it('resolves namespace-prefixed attributes by local name when the prefix differs', () => {
    const node = parseXml(`
      <root xmlns:rel="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            rel:id="rId7"/>
    `);

    expect(node.attr('r:id')).toBe('rId7');
  });

  it('keeps exact unprefixed attributes ahead of namespace-local fallback', () => {
    const node = parseXml(`
      <root xmlns:rel="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            id="local-id"
            rel:id="rId7"/>
    `);

    expect(node.attr('id')).toBe('local-id');
    expect(node.attr('r:id')).toBe('rId7');
  });
});
