import { expect, test } from '@playwright/test';

test.use({ channel: process.env.PLAYWRIGHT_CHANNEL });

for (const family of ['scatter', 'bubble']) {
  for (const mode of ['gap', 'span', 'zero']) {
    test(`real ECharts ${family} ${mode} retains pairs and blanks`, async ({ page }) => {
      await page.goto('/test/browser/blank.html');
      const result = await page.evaluate(
        async ({ family, mode }) => {
          const { chartOption, xyChartXml } =
            await import('/test/fixtures/chart-table-coverage.ts');
          const { echarts } = await import('/src/renderer/chart/echartsRuntime.ts');
          const host = document.createElement('div');
          Object.assign(host.style, { width: '640px', height: '360px' });
          document.body.append(host);
          const chart = echarts.init(host, undefined, { renderer: 'canvas' });
          chart.setOption({ ...chartOption(xyChartXml(family, mode)), animation: false });
          const model = chart.getModel().getSeriesByIndex(0);
          const data = model.getData();
          const points = Array.from({ length: data.count() }, (_, i) => data.getValues(i));
          const drawable = Array.from(
            { length: data.count() },
            (_, i) => !!data.getItemGraphicEl(i),
          );
          chart.getZr().flush();
          const canvas = host.querySelector('canvas')!;
          const pixels = canvas
            .getContext('2d')!
            .getImageData(0, 0, canvas.width, canvas.height).data;
          const painted = pixels.some((v, i) => i % 4 === 3 && v > 0);
          const polyline = chart
            .getZr()
            .storage.getDisplayList()
            .find((item) => item.type === 'ec-polyline');
          let segments = 0;
          if (polyline)
            polyline.buildPath(
              { moveTo: () => segments++, lineTo() {}, bezierCurveTo() {} },
              polyline.shape,
            );
          const connectNulls = model.get('connectNulls');
          chart.dispose();
          return { points, drawable, painted, connectNulls, segments };
        },
        { family, mode },
      );
      expect(result.points[0].slice(0, 2)).toEqual([1, 10]);
      expect(result.points[1].slice(0, 2)).toEqual([2, mode === 'zero' ? 0 : NaN]);
      expect(result.points[2].slice(0, 2)).toEqual([3, 0]);
      expect(result.points[3].slice(0, 2)).toEqual([mode === 'zero' ? 0 : NaN, 40]);
      expect(result.drawable[1]).toBe(mode === 'zero');
      expect(result.drawable[2]).toBe(true);
      expect(result.painted).toBe(true);
      if (family === 'scatter') expect(result.segments).toBe(mode === 'gap' ? 3 : 1);
      if (family === 'scatter') expect(result.connectNulls).toBe(mode === 'span');
    });
  }
}

for (const scenario of [
  { chartTag: 'bar3DChart', expectedType: 'bar' },
  { chartTag: 'pie3DChart', expectedType: 'pie' },
]) {
  test(`real ECharts ${scenario.chartTag} preserves readable fallback data`, async ({ page }) => {
    await page.goto('/test/browser/blank.html');
    const result = await page.evaluate(async ({ chartTag, expectedType }) => {
      const { cacheXml, chartOption } = await import('/test/fixtures/chart-table-coverage.ts');
      const { echarts } = await import('/src/renderer/chart/echartsRuntime.ts');
      const axes = chartTag === 'bar3DChart' ? '<c:axId val="1"/><c:axId val="2"/>' : '';
      const xml = `<c:chartSpace
        xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
        xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <c:chart>
          <c:view3D><c:rotX val="20"/><c:rotY val="30"/><c:perspective val="30"/></c:view3D>
          <c:plotArea>
            <c:${chartTag}>
              ${chartTag === 'bar3DChart' ? '<c:barDir val="col"/><c:grouping val="clustered"/>' : '<c:varyColors val="1"/>'}
              <c:ser><c:idx val="0"/><c:order val="0"/><c:tx><c:v>Revenue</c:v></c:tx>
                <c:cat>${cacheXml('strLit', ['A', 'B', 'C'])}</c:cat>
                <c:val>${cacheXml('numLit', [4, 7, 3])}</c:val>
              </c:ser>${axes}
            </c:${chartTag}>
            <c:catAx><c:axId val="1"/><c:axPos val="b"/><c:crossAx val="2"/></c:catAx>
            <c:valAx><c:axId val="2"/><c:axPos val="l"/><c:crossAx val="1"/></c:valAx>
          </c:plotArea>
        </c:chart>
      </c:chartSpace>`;
      const option = chartOption(xml);
      const optionSeries = (option.series as any[])[0];
      const host = document.createElement('div');
      Object.assign(host.style, { width: '640px', height: '360px' });
      document.body.append(host);
      const chart = echarts.init(host, undefined, { renderer: 'canvas' });
      chart.setOption({ ...option, animation: false });
      chart.getZr().flush();
      const data = chart.getModel().getSeriesByIndex(0).getData();
      const canvas = host.querySelector('canvas')!;
      const pixels = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
      const answer = {
        expectedType,
        type: optionSeries.type,
        name: optionSeries.name,
        values: optionSeries.data.map((item: any) =>
          typeof item === 'number' ? item : item.value,
        ),
        categories: Array.from({ length: data.count() }, (_, index) => data.getName(index)),
        painted: pixels.some((value, index) => index % 4 === 3 && value > 0),
      };
      chart.dispose();
      return answer;
    }, scenario);

    expect(result.type).toBe(result.expectedType);
    expect(result.name).toBe('Revenue');
    expect(result.values).toEqual([4, 7, 3]);
    expect(result.categories).toEqual(['A', 'B', 'C']);
    expect(result.painted).toBe(true);
  });
}

test('browser table merged edges, explicit clearing, corners and direct precedence', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { tableFixture, borderXml, cornerStyles } =
      await import('/test/fixtures/chart-table-coverage.ts');
    const base = `<a:wholeTbl><a:tcStyle><a:tcBdr>${borderXml('bottom', 'FF0000')}${borderXml('right', 'FF0000')}${borderXml('insideH', '0000FF')}${borderXml('insideV', '0000FF')}</a:tcBdr></a:tcStyle></a:wholeTbl>`;
    const merged = tableFixture(base, '', [
      '<a:tc rowSpan="2" gridSpan="2"/><a:tc hMerge="1"/>',
      '<a:tc vMerge="1"/><a:tc vMerge="1"/>',
    ]);
    const cleared = tableFixture(
      base +
        '<a:firstRow><a:tcStyle><a:tcBdr><a:bottom><a:ln><a:noFill/></a:ln></a:bottom></a:tcBdr></a:tcStyle></a:firstRow>',
      'firstRow="1"',
      ['<a:tc/><a:tc/>'],
    );
    const corner = tableFixture(
      cornerStyles(),
      'firstRow="1" firstCol="1" lastRow="1" lastCol="1"',
    );
    for (const element of [merged, cleared, corner]) {
      const parent = document.createElement('div');
      Object.assign(parent.style, {
        position: 'relative',
        width: '420px',
        height: '220px',
        transform: 'scale(0.8)',
        transformOrigin: 'top left',
      });
      parent.append(element);
      document.body.append(parent);
    }
    const cell = merged.querySelector('td')!;
    return {
      bottom: getComputedStyle(cell).borderBottomColor,
      right: getComputedStyle(cell).borderRightColor,
      width: merged.getBoundingClientRect().width,
      clear: getComputedStyle(cleared.querySelector('td')!).borderBottomStyle,
      colors: Array.from(
        corner.querySelectorAll('td'),
        (td) => getComputedStyle(td).backgroundColor,
      ),
    };
  });
  expect(result.bottom).toBe('rgb(255, 0, 0)');
  expect(result.right).toBe('rgb(255, 0, 0)');
  expect(result.clear).toBe('none');
  expect(result.width).toBeCloseTo(320);
  expect(result.colors).toEqual([
    'rgb(255, 0, 0)',
    'rgb(0, 255, 0)',
    'rgb(0, 0, 255)',
    'rgb(255, 255, 0)',
  ]);
});

for (const invert of [
  '',
  '<c:invertIfNegative/>',
  '<c:invertIfNegative val="1"/>',
  '<c:invertIfNegative val="0"/>',
]) {
  test(`real ECharts literal negative bars ${invert || 'missing invertIfNegative'}`, async ({
    page,
  }) => {
    await page.goto('/test/browser/blank.html');
    const result = await page.evaluate(async (invert) => {
      const { chartOption, chartXml, cacheXml } =
        await import('/test/fixtures/chart-table-coverage.ts');
      const { echarts } = await import('/src/renderer/chart/echartsRuntime.ts');
      const xml = chartXml(
        `<c:barChart><c:barDir val="col"/><c:ser>${invert}<c:spPr><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill></c:spPr><c:cat><c:strRef><c:strCache/></c:strRef>${cacheXml('strLit', ['A', 'B'])}</c:cat><c:val><c:numRef/>${cacheXml('numLit', [2, -3])}</c:val></c:ser></c:barChart>`,
      );
      const host = document.createElement('div');
      Object.assign(host.style, { width: '640px', height: '360px' });
      document.body.append(host);
      const chart = echarts.init(host);
      chart.setOption({ ...chartOption(xml), animation: false });
      chart.getZr().flush();
      const data = chart.getModel().getSeriesByIndex(0).getData();
      const bar = data.getItemGraphicEl(1);
      const answer = {
        categories: [data.getName(0), data.getName(1)],
        fill: bar?.style.fill ?? null,
        stroke: bar?.style.stroke ?? null,
        height: bar ? Math.abs(bar.shape.height) : 0,
        value: data.get('y', 1),
      };
      chart.dispose();
      return answer;
    }, invert);
    expect(result.categories).toEqual(['A', 'B']);
    expect(result.value).toBe(-3);
    expect(result.height).toBeGreaterThan(0);
    expect(result.fill).toBe(invert.includes('val="0"') ? '#FF0000' : '#FFFFFF');
    if (!invert.includes('val="0"')) expect(result.stroke).toBe('#000000');
  });
}

test('horizontal negative bars keep category labels next to the zero-crossing value axis', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { applyZeroCrossingAxisLabelLayout, parseChartXml } =
      await import('/src/renderer/ChartRenderer.ts');
    const { echarts } = await import('/src/renderer/chart/echartsRuntime.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const xml = `<c:chartSpace
      xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <c:chart><c:autoTitleDeleted val="1"/><c:plotArea>
        <c:barChart><c:barDir val="bar"/><c:grouping val="clustered"/><c:varyColors val="0"/>
          <c:ser><c:idx val="0"/><c:order val="0"/><c:invertIfNegative val="0"/>
            <c:cat><c:strLit><c:ptCount val="2"/><c:pt idx="0"><c:v>Loss</c:v></c:pt><c:pt idx="1"><c:v>Gain</c:v></c:pt></c:strLit></c:cat>
            <c:val><c:numLit><c:ptCount val="2"/><c:pt idx="0"><c:v>-3</c:v></c:pt><c:pt idx="1"><c:v>5</c:v></c:pt></c:numLit></c:val>
          </c:ser><c:axId val="10"/><c:axId val="20"/>
        </c:barChart>
        <c:catAx><c:axId val="10"/><c:axPos val="l"/><c:tickLblPos val="nextTo"/><c:crossAx val="20"/><c:crosses val="autoZero"/></c:catAx>
        <c:valAx><c:axId val="20"/><c:scaling><c:min val="-4"/><c:max val="6"/></c:scaling><c:axPos val="b"/><c:crossAx val="10"/><c:crosses val="autoZero"/></c:valAx>
      </c:plotArea></c:chart>
    </c:chartSpace>`;
    const option = parseChartXml(parseXml(xml), createMockRenderContext(), undefined, {
      w: 640,
      h: 360,
    }).option;
    applyZeroCrossingAxisLabelLayout(option, { w: 640, h: 360 });
    const host = document.createElement('div');
    Object.assign(host.style, { width: '640px', height: '360px' });
    document.body.append(host);
    const chart = echarts.init(host, undefined, { renderer: 'canvas' });
    chart.setOption({ ...option, animation: false });
    chart.getZr().flush();
    const grid = (chart.getModel().getComponent('grid', 0) as any).coordinateSystem.getRect();
    const zeroX = chart.convertToPixel({ xAxisIndex: 0 }, 0) as number;
    const labels = chart
      .getZr()
      .storage.getDisplayList()
      .filter((element: any) => ['Loss', 'Gain'].includes(String(element.style?.text ?? '')))
      .map((element: any) => {
        const rect = element.getPaintRect();
        return { left: rect.x, right: rect.x + rect.width };
      });
    chart.dispose();
    return {
      gridLeft: grid.x,
      zeroX,
      labels,
      configuredMargin: (option.yAxis as any).axisLabel?.margin,
    };
  });

  expect(result.labels).toHaveLength(2);
  const rightmostLabel = Math.max(...result.labels.map((label) => label.right));
  expect(rightmostLabel).toBeLessThan(result.zeroX);
  expect(rightmostLabel).toBeGreaterThan(result.zeroX - 40);
  expect(result.zeroX).toBeGreaterThan(result.gridLeft + 40);
  expect(result.configuredMargin).toBeLessThan(0);
});
