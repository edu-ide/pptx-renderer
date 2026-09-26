import { describe, expect, it } from 'vitest';
import { buildDataTableElement, parseDataTable } from '../../../../src/renderer/chart/dataTable';
import { parseXml } from '../../../../src/parser/XmlParser';

describe('chart data table helpers', () => {
  it('parses dTable showKeys with OOXML false aliases', () => {
    const plotArea = parseXml(`
      <c:plotArea xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
        <c:dTable><c:showKeys val="false"/></c:dTable>
      </c:plotArea>
    `);

    expect(parseDataTable(plotArea)).toEqual({ showKeys: false });
  });

  it('renders blank cells for missing series values', () => {
    const table = buildDataTableElement({
      showKeys: true,
      seriesArr: [
        { name: 'S1', order: 0, categories: ['A', 'B'], values: [1] },
        { name: 'S2', order: 1, categories: ['A', 'B'], values: [2, 3] },
      ],
    });

    expect(table.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(table.querySelectorAll('tbody tr')[0].children[2].textContent).toBe('');
  });

  it('renders blank cells for sparse cache points tracked by blankIndices', () => {
    const table = buildDataTableElement({
      showKeys: false,
      seriesArr: [
        {
          name: 'S1',
          order: 0,
          categories: ['A', 'B', 'C'],
          values: [5, 0, 7],
          blankIndices: new Set([1]),
        },
      ],
    });

    const cells = Array.from(table.querySelectorAll('tbody td')).map((td) => td.textContent);

    expect(cells).toEqual(['S1', '5', '', '7']);
  });

  it('formats data table cells with each series format code by default', () => {
    const table = buildDataTableElement({
      showKeys: false,
      seriesArr: [
        { name: 'Percent', order: 0, categories: ['A'], values: [0.25], formatCode: '0%' },
        { name: 'Count', order: 1, categories: ['A'], values: [1234], formatCode: '#,##0' },
      ],
    });

    const cells = Array.from(table.querySelectorAll('tbody td')).map((td) => td.textContent);

    expect(cells).toEqual(['Percent', '25%', 'Count', '1,234']);
  });
});
