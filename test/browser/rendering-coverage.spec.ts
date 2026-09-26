import { expect, test, type Locator, type Page } from '@playwright/test';
import { PNG } from 'pngjs';

// Set PLAYWRIGHT_CHANNEL=chrome on machines with Chrome but no downloaded Chromium.
test.use({ channel: process.env.PLAYWRIGHT_CHANNEL });

async function expectStableScreenshot(page: Page, locator: Locator): Promise<void> {
  let previous = await locator.screenshot();
  await expect
    .poll(
      async () => {
        await page.evaluate(
          () =>
            new Promise<void>((resolve) =>
              requestAnimationFrame(() => requestAnimationFrame(resolve)),
            ),
        );
        const current = await locator.screenshot();
        const stable = previous.equals(current);
        previous = current;
        return stable;
      },
      { timeout: 5000 },
    )
    .toBe(true);
}

test('browser accepts deterministic OOXML runtime geometry and bounded donut adjustments', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const { results, multiPathResults, donutResults, groupedDonut, pictureClipD } =
    await page.evaluate(async () => {
      const { getPresetShapePath } = await import('/src/shapes/presets.ts');
      const { parseXml } = await import('/src/parser/XmlParser.ts');
      const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
      const { parsePicNode } = await import('/src/model/nodes/PicNode.ts');
      const { parseGroupNode } = await import('/src/model/nodes/GroupNode.ts');
      const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
      const { renderImage } = await import('/src/renderer/ImageRenderer.ts');
      const { renderGroup } = await import('/src/renderer/GroupRenderer.ts');
      const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
      const { ooxmlPresetRuntimeMultiPathShapeNames, ooxmlPresetRuntimeShapeNames } =
        await import('/src/shapes/ooxmlGeometryRuntime.ts');
      const results = ooxmlPresetRuntimeShapeNames.map((name) => {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        const d = getPresetShapePath(name, 400, 280);
        svg.setAttribute('viewBox', '0 0 400 280');
        path.setAttribute('d', d);
        svg.append(path);
        document.body.append(svg);
        const box = path.getBBox();
        return { name, d, x: box.x, y: box.y, width: box.width, height: box.height };
      });
      const multiPathResults = ooxmlPresetRuntimeMultiPathShapeNames.map((name) => {
        const xml = `<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
          xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="1" name="Flowchart"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="3810000" cy="2667000"/></a:xfrm>
          <a:prstGeom prst="${name}"><a:avLst/></a:prstGeom>
          <a:solidFill><a:srgbClr val="4472C4"/></a:solidFill>
          <a:ln w="12700"><a:solidFill><a:srgbClr val="203864"/></a:solidFill></a:ln>
        </p:spPr>
      </p:sp>`;
        const rendered = renderShape(parseShapeNode(parseXml(xml)), createMockRenderContext());
        document.body.append(rendered);
        return {
          name,
          paths: Array.from(rendered.querySelectorAll('svg > path')).map((path) => ({
            d: path.getAttribute('d'),
            fill: path.getAttribute('fill'),
            stroke: path.getAttribute('stroke'),
          })),
        };
      });
      const donutResults = [0, 10000, 25000, 50000].map((adjustment) => {
        const xml = `<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
          xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="18" name="Donut"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="3810000" cy="1714500"/></a:xfrm>
          <a:prstGeom prst="donut"><a:avLst><a:gd name="adj" fmla="val ${adjustment}"/></a:avLst></a:prstGeom>
          <a:solidFill><a:srgbClr val="4472C4"/></a:solidFill>
        </p:spPr>
      </p:sp>`;
        const rendered = renderShape(parseShapeNode(parseXml(xml)), createMockRenderContext());
        document.body.append(rendered);
        const path = rendered.querySelector('svg > path')!;
        const box = path.getBBox();
        return {
          adjustment,
          d: path.getAttribute('d') ?? '',
          box: { x: box.x, y: box.y, width: box.width, height: box.height },
        };
      });

      const groupXml = `<p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
        xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:nvGrpSpPr><p:cNvPr id="40" name="Donut group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="1905000"/><a:chOff x="0" y="0"/><a:chExt cx="3810000" cy="952500"/></a:xfrm></p:grpSpPr>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="18" name="Grouped donut"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/></a:xfrm><a:prstGeom prst="donut"><a:avLst><a:gd name="adj" fmla="val 10000"/></a:avLst></a:prstGeom><a:solidFill><a:srgbClr val="4472C4"/></a:solidFill></p:spPr>
      </p:sp>
    </p:grpSp>`;
      const group = renderGroup(
        parseGroupNode(parseXml(groupXml)),
        createMockRenderContext(),
        (node, context) => renderShape(node as Parameters<typeof renderShape>[0], context),
      );
      document.body.append(group);
      const groupedShape = group.firstElementChild as HTMLElement;
      const groupedDonut = {
        width: groupedShape.getBoundingClientRect().width,
        height: groupedShape.getBoundingClientRect().height,
        d: groupedShape.querySelector('svg > path')?.getAttribute('d') ?? '',
      };

      const pictureXml = `<p:pic xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
        xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
        xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <p:nvPicPr><p:cNvPr id="19" name="Donut picture"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
      <p:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
      <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/></a:xfrm><a:prstGeom prst="donut"><a:avLst><a:gd name="adj" fmla="val 10000"/></a:avLst></a:prstGeom></p:spPr>
    </p:pic>`;
      const pictureContext = createMockRenderContext();
      pictureContext.slide.rels.set('rId1', { type: 'image', target: 'ppt/media/image1.png' });
      pictureContext.presentation.media.set(
        'ppt/media/image1.png',
        new Uint8Array([0x89, 0x50, 0x4e, 0x47]),
      );
      const picture = renderImage(parsePicNode(parseXml(pictureXml)), pictureContext);
      document.body.append(picture);
      const pictureClipD = picture.querySelector('clipPath path')?.getAttribute('d') ?? '';

      return { results, multiPathResults, donutResults, groupedDonut, pictureClipD };
    });

  expect(results).toHaveLength(29);
  for (const result of results) {
    expect(result.d, result.name).not.toMatch(/NaN|Infinity/);
    expect(result.width, result.name).toBeGreaterThan(0);
    expect(result.height, result.name).toBeGreaterThan(0);
    expect([result.x, result.y, result.width, result.height].every(Number.isFinite)).toBe(true);
  }
  expect(results.find(({ name }) => name === 'flowChartTerminator')?.d).toBe(
    'M64.351852,0 L335.648148,0 A64.351852,140 0 0,1 335.648148,280 L64.351852,280 A64.351852,140 0 0,1 64.351852,0 Z',
  );
  expect(multiPathResults).toHaveLength(8);
  for (const result of multiPathResults) {
    expect(result.paths, result.name).toHaveLength(3);
    expect(result.paths.map(({ fill }) => fill)).toEqual(['#4472C4', 'none', 'none']);
    expect(result.paths[0].stroke).toBe('none');
    expect(result.paths[1].stroke).toBe('#203864');
    expect(result.paths[2].stroke).toBe(
      result.name === 'flowChartMultidocument' ? 'none' : '#203864',
    );
    expect(result.paths.every(({ d }) => !!d && !/NaN|Infinity/.test(d))).toBe(true);
  }
  for (const result of donutResults) {
    expect(result.d.match(/M/g), `donut adj=${result.adjustment}`).toHaveLength(2);
    expect(result.d, `donut adj=${result.adjustment}`).not.toMatch(/NaN|Infinity/);
    expect(result.box).toEqual({ x: 0, y: 0, width: 400, height: 180 });
  }
  expect(groupedDonut).toMatchObject({ width: 100, height: 200 });
  expect(groupedDonut.d).toContain('M10,100 A40,90');
  expect(pictureClipD).toContain('M10,50 A90,40');
});

test('bounded ordinary outer shadows keep native scale anchors and visible filter bounds', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { parseGroupNode } = await import('/src/model/nodes/GroupNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { renderGroup } = await import('/src/renderer/GroupRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');

    const shapeXml = (id: number, x: number, shadow: string, preset = 'rect') => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="${id}" name="Shadow ${id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="${x * 9525}" y="381000"/><a:ext cx="2286000" cy="1143000"/></a:xfrm>
          <a:prstGeom prst="${preset}"><a:avLst/></a:prstGeom>
          <a:solidFill><a:srgbClr val="4472C4"/></a:solidFill>
          <a:ln><a:noFill/></a:ln>
          <a:effectLst>${shadow}</a:effectLst>
        </p:spPr>
      </p:sp>`;
    const scaledUp = renderShape(
      parseShapeNode(
        parseXml(
          shapeXml(
            1,
            40,
            '<a:outerShdw blurRad="115455" dist="46182" sx="102000" sy="102000" algn="ctr" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="35000"/></a:srgbClr></a:outerShdw>',
          ),
        ),
      ),
      createMockRenderContext(),
    );
    const scaledDown = renderShape(
      parseShapeNode(
        parseXml(
          shapeXml(
            2,
            360,
            '<a:outerShdw blurRad="317500" dist="127000" dir="8100000" sx="92000" sy="92000" algn="tr" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="35000"/></a:srgbClr></a:outerShdw>',
          ),
        ),
      ),
      createMockRenderContext(),
    );
    const groupXml = `
      <p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvGrpSpPr><p:cNvPr id="10" name="Rotated group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
        <p:grpSpPr><a:xfrm rot="2700000"><a:off x="6477000" y="381000"/><a:ext cx="2286000" cy="1143000"/><a:chOff x="0" y="0"/><a:chExt cx="2286000" cy="1143000"/></a:xfrm></p:grpSpPr>
        ${shapeXml(
          3,
          0,
          '<a:outerShdw blurRad="115455" dist="46182" sx="102000" sy="102000" algn="ctr" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="35000"/></a:srgbClr></a:outerShdw>',
        )}
      </p:grpSp>`;
    const rotatedGroup = renderGroup(
      parseGroupNode(parseXml(groupXml)),
      createMockRenderContext(),
      (node, context) => renderShape(node as Parameters<typeof renderShape>[0], context),
    );
    const uniformGroupXml = `
      <p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvGrpSpPr><p:cNvPr id="11" name="Uniform group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
        <p:grpSpPr><a:xfrm><a:off x="6191250" y="381000"/><a:ext cx="2857500" cy="1428750"/><a:chOff x="0" y="0"/><a:chExt cx="2286000" cy="1143000"/></a:xfrm></p:grpSpPr>
        ${shapeXml(
          4,
          0,
          '<a:outerShdw blurRad="76200" dist="50800" dir="2700000" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="35000"/></a:srgbClr></a:outerShdw>',
          'roundRect',
        )}
      </p:grpSp>`;
    const uniformGroup = renderGroup(
      parseGroupNode(parseXml(uniformGroupXml)),
      createMockRenderContext(),
      (node, context) => renderShape(node as Parameters<typeof renderShape>[0], context),
    );

    const host = document.createElement('div');
    host.id = 'outer-shadow-browser-host';
    host.style.position = 'relative';
    host.style.width = '960px';
    host.style.height = '240px';
    host.style.background = '#fff';
    host.append(scaledUp, scaledDown, rotatedGroup, uniformGroup);
    document.body.append(host);

    const inspect = (element: HTMLElement) => {
      const svg = element.querySelector('svg')!;
      const group = svg.querySelector<SVGGElement>(
        ':scope > g[data-pptx-outer-shadow="scaled-silhouette"]',
      )!;
      const main = svg.querySelector<SVGPathElement>(':scope > path')!;
      const filter = svg.querySelector<SVGFilterElement>('filter[id^="shape-shadow-blur-"]')!;
      return {
        scaleX: group.getAttribute('data-pptx-shadow-scale-x'),
        scaleY: group.getAttribute('data-pptx-shadow-scale-y'),
        alignment: group.getAttribute('data-pptx-shadow-alignment'),
        anchorX: Number(group.getAttribute('data-pptx-shadow-anchor-x')),
        anchorY: Number(group.getAttribute('data-pptx-shadow-anchor-y')),
        width: Number(svg.getAttribute('width')),
        height: Number(svg.getAttribute('height')),
        mainFilter: main.getAttribute('filter'),
        shadowBeforeMain:
          Array.from(svg.children).indexOf(group) < Array.from(svg.children).indexOf(main),
        filterExpandsLeft: Number(filter.getAttribute('x')) < 0,
        filterExpandsTop: Number(filter.getAttribute('y')) < 0,
        filterExpandsRight:
          Number(filter.getAttribute('width')) > Number(svg.getAttribute('width')),
        filterExpandsBottom:
          Number(filter.getAttribute('height')) > Number(svg.getAttribute('height')),
        overflow: svg.style.overflow,
      };
    };

    return {
      scaledUp: inspect(scaledUp),
      scaledDown: inspect(scaledDown),
      rotatedGroupHasClone: !!rotatedGroup.querySelector(
        '[data-pptx-outer-shadow="scaled-silhouette"]',
      ),
      rotatedGroupFallback: rotatedGroup.querySelector('svg > path')?.getAttribute('filter'),
      uniformGroupHasClone: !!uniformGroup.querySelector(
        '[data-pptx-outer-shadow="scaled-silhouette"]',
      ),
      uniformGroupFilter: uniformGroup.querySelector('svg > path')?.getAttribute('filter'),
      uniformGroupStdDeviation: uniformGroup
        .querySelector('feDropShadow')
        ?.getAttribute('stdDeviation'),
    };
  });

  expect(result.scaledUp).toMatchObject({
    scaleX: '1.02',
    scaleY: '1.02',
    alignment: 'ctr',
    mainFilter: null,
    shadowBeforeMain: true,
    filterExpandsLeft: true,
    filterExpandsTop: true,
    filterExpandsRight: true,
    filterExpandsBottom: true,
    overflow: 'visible',
  });
  expect(result.scaledUp.anchorX).toBeCloseTo(result.scaledUp.width / 2, 8);
  expect(result.scaledUp.anchorY).toBeCloseTo(result.scaledUp.height / 2, 8);
  expect(result.scaledDown).toMatchObject({
    scaleX: '0.92',
    scaleY: '0.92',
    alignment: 'tr',
    anchorY: 0,
    mainFilter: null,
    shadowBeforeMain: true,
    overflow: 'visible',
  });
  expect(result.scaledDown.anchorX).toBeCloseTo(result.scaledDown.width, 8);
  expect(result.rotatedGroupHasClone).toBe(false);
  expect(result.rotatedGroupFallback).toMatch(/^url\(#shape-shadow-/);
  expect(result.uniformGroupHasClone).toBe(false);
  expect(result.uniformGroupFilter).toMatch(/^url\(#shape-shadow-/);
  expect(result.uniformGroupStdDeviation).toBe('4.00');

  await expectStableScreenshot(page, page.locator('#outer-shadow-browser-host'));
});

test('shape reflections stay in local coordinates and isolate cloned SVG references', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');

    const shapeXml = (id: number, y: number) => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="${id}" name="Reflection ${id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="381000" y="${y * 9525}"/><a:ext cx="1905000" cy="762000"/></a:xfrm>
          <a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>
          <a:gradFill>
            <a:gsLst>
              <a:gs pos="0"><a:srgbClr val="5B9BD5"/></a:gs>
              <a:gs pos="100000"><a:srgbClr val="2F5597"/></a:gs>
            </a:gsLst>
            <a:lin ang="5400000" scaled="1"/>
          </a:gradFill>
          <a:ln><a:noFill/></a:ln>
          <a:effectLst>
            <a:reflection blurRad="9525" stA="52000" endA="300" fadeDir="0"
                          stPos="0" endPos="35000" dist="76200" dir="5400000"
                          sy="-100000" algn="bl" rotWithShape="0"/>
          </a:effectLst>
        </p:spPr>
      </p:sp>`;
    const first = renderShape(parseShapeNode(parseXml(shapeXml(1, 20))), createMockRenderContext());
    const second = renderShape(
      parseShapeNode(parseXml(shapeXml(2, 220))),
      createMockRenderContext(),
    );

    const host = document.createElement('div');
    host.id = 'reflection-browser-host';
    host.style.position = 'relative';
    host.style.width = '320px';
    host.style.height = '420px';
    host.style.background = '#fff';
    host.append(first, second);
    document.body.append(host);

    const inspect = (element: HTMLElement) => {
      const layer = element.querySelector<HTMLElement>(
        ':scope > [data-pptx-reflection-layer="true"]',
      )!;
      const source = layer.querySelector<HTMLElement>(
        ':scope > [data-pptx-reflection-source="true"]',
      )!;
      const wrapperRect = element.getBoundingClientRect();
      const layerRect = layer.getBoundingClientRect();
      const originalGradient = element.querySelector<SVGLinearGradientElement>(
        ':scope > svg linearGradient',
      )!;
      const clonedGradient = source.querySelector<SVGLinearGradientElement>('linearGradient')!;
      const clonedPath = source.querySelector<SVGPathElement>('svg path')!;
      return {
        wrapperTop: wrapperRect.top,
        relativeLayerTop: layerRect.top - wrapperRect.top,
        layerStyleTop: layer.style.top,
        sourceLeft: source.style.left,
        sourceTop: source.style.top,
        sourceTransform: source.style.transform,
        maskImage: layer.style.maskImage,
        legacyReflect: element.style.getPropertyValue('-webkit-box-reflect'),
        originalGradientId: originalGradient.id,
        clonedGradientId: clonedGradient.id,
        clonedPathFill: clonedPath.getAttribute('fill'),
      };
    };

    return { first: inspect(first), second: inspect(second) };
  });

  expect(result.second.wrapperTop - result.first.wrapperTop).toBeCloseTo(200, 4);
  expect(result.first.relativeLayerTop).toBeCloseTo(88, 4);
  expect(result.second.relativeLayerTop).toBeCloseTo(result.first.relativeLayerTop, 8);
  for (const reflection of [result.first, result.second]) {
    expect(reflection).toMatchObject({
      layerStyleTop: '88px',
      sourceLeft: '0px',
      sourceTop: '0px',
      legacyReflect: '',
    });
    expect(reflection.sourceTransform).toContain('matrix(1, 0, 0, -1');
    expect(reflection.maskImage).toContain('90deg');
    expect(reflection.clonedGradientId).not.toBe(reflection.originalGradientId);
    expect(reflection.clonedPathFill).toBe(`url(#${reflection.clonedGradientId})`);
  }

  await expectStableScreenshot(page, page.locator('#reflection-browser-host'));
});

test('bounded static DrawingML 3D stays stable across shapes, pictures, groups, and disposal', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { parsePicNode } = await import('/src/model/nodes/PicNode.ts');
    const { parseGroupNode } = await import('/src/model/nodes/GroupNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { renderGroup } = await import('/src/renderer/GroupRenderer.ts');
    const { renderSlide } = await import('/src/renderer/SlideRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');

    const scene =
      '<a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>';
    const bevel = '<a:sp3d><a:bevelT w="127000" h="127000" prst="circle"/></a:sp3d>';
    const shapeXml = (preset: string, width: number, height: number, color: string) => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="1" name="3D ${preset}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="${width * 9525}" cy="${height * 9525}"/></a:xfrm>
          <a:prstGeom prst="${preset}"><a:avLst/></a:prstGeom>
          <a:solidFill><a:srgbClr val="${color}"/></a:solidFill>${scene}${bevel}
        </p:spPr>
      </p:sp>`;

    document.body.style.margin = '0';
    const host = document.createElement('div');
    host.id = 'shape3d-browser-host';
    Object.assign(host.style, {
      display: 'flex',
      flexWrap: 'wrap',
      gap: '20px',
      width: '900px',
      padding: '20px',
      overflow: 'auto',
      background: 'white',
    });
    document.body.append(host);
    const ctx = createMockRenderContext();
    const shape3dTasks: Promise<void>[] = [];
    for (const spec of [
      ['rect', 260, 80, '2F75B5'],
      ['rect', 80, 220, '2F75B5'],
      ['roundRect', 220, 100, '2F75B5'],
      ['ellipse', 180, 120, '2F75B5'],
      ['rect', 240, 100, '70AD47'],
    ] as const) {
      const shape = renderShape(
        parseShapeNode(parseXml(shapeXml(...spec))),
        createMockRenderContext({ asyncTasks: shape3dTasks }),
      );
      shape.style.position = 'relative';
      shape.style.left = '0';
      shape.style.top = '0';
      host.append(shape);
    }

    const donut = renderShape(
      parseShapeNode(
        parseXml(
          shapeXml('donut', 180, 120, '2F75B5').replace(
            '<a:avLst/>',
            '<a:avLst><a:gd name="adj" fmla="val 32000"/></a:avLst>',
          ),
        ),
      ),
      createMockRenderContext({ asyncTasks: shape3dTasks }),
    );
    donut.style.position = 'relative';
    donut.style.left = '0';
    donut.style.top = '0';
    host.append(donut);

    const flat = renderShape(
      parseShapeNode(
        parseXml(
          shapeXml('ellipse', 120, 80, '2F75B5').replace('orthographicFront', 'perspectiveFront'),
        ),
      ),
      createMockRenderContext(),
    );
    flat.style.position = 'relative';
    flat.style.left = '0';
    flat.style.top = '0';
    host.append(flat);

    const groupXml = `
      <p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvGrpSpPr><p:cNvPr id="20" name="3D group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
        <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/><a:chOff x="0" y="0"/><a:chExt cx="3810000" cy="1905000"/></a:xfrm></p:grpSpPr>
        ${shapeXml('rect', 200, 100, '70AD47')}
      </p:grpSp>`;
    const group = renderGroup(
      parseGroupNode(parseXml(groupXml)),
      createMockRenderContext({ asyncTasks: shape3dTasks }),
      (node, context) => renderShape(node as Parameters<typeof renderShape>[0], context),
    );
    group.style.position = 'relative';
    group.style.left = '0';
    group.style.top = '0';
    host.append(group);

    const groupedDonutXml = `
      <p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvGrpSpPr><p:cNvPr id="21" name="3D donut group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
        <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="952500" cy="1905000"/><a:chOff x="0" y="0"/><a:chExt cx="1905000" cy="1905000"/></a:xfrm></p:grpSpPr>
        ${shapeXml('donut', 200, 200, '70AD47')}
      </p:grpSp>`;
    const groupedDonut = renderGroup(
      parseGroupNode(parseXml(groupedDonutXml)),
      createMockRenderContext({ asyncTasks: shape3dTasks }),
      (node, context) => renderShape(node as Parameters<typeof renderShape>[0], context),
    );
    groupedDonut.style.position = 'relative';
    groupedDonut.style.left = '0';
    groupedDonut.style.top = '0';
    host.append(groupedDonut);

    const png = Uint8Array.from(
      atob(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z2S8AAAAASUVORK5CYII=',
      ),
      (char) => char.charCodeAt(0),
    );
    const pictureXml = `
      <p:pic xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <p:nvPicPr><p:cNvPr id="30" name="3D picture"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="rId1"/><a:srcRect l="22000" t="18000" r="8000" b="12000"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>${scene}${bevel}
        </p:spPr>
      </p:pic>`;
    ctx.slide.rels.set('rId1', { type: 'image', target: 'ppt/media/image1.png' });
    ctx.presentation.media.set('ppt/media/image1.png', png);
    ctx.slide.nodes = [parsePicNode(parseXml(pictureXml))];
    const revoked: string[] = [];
    const originalRevoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = (url: string) => {
      revoked.push(url);
      originalRevoke(url);
    };
    const handle = renderSlide(ctx.presentation, ctx.slide);
    handle.element.style.position = 'absolute';
    handle.element.style.left = '-2000px';
    document.body.append(handle.element);
    await handle.ready;
    const disposableHadBevel = !!handle.element.querySelector('[data-pptx-shape3d-bevel]');
    const disposableHadLighting = !!handle.element.querySelector(
      '[data-pptx-shape3d-lighting="distance-field"]',
    );
    const croppedPicture = handle.element.querySelector('svg image');
    const croppedPictureBounds = croppedPicture
      ? {
          x: Number(croppedPicture.getAttribute('x')),
          y: Number(croppedPicture.getAttribute('y')),
          width: Number(croppedPicture.getAttribute('width')),
          height: Number(croppedPicture.getAttribute('height')),
        }
      : null;
    handle.dispose();
    URL.revokeObjectURL = originalRevoke;
    handle.element.remove();

    await Promise.all(shape3dTasks);
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    const ids = Array.from(host.querySelectorAll('[id]'), (element) => element.id);
    const groupChild = group.firstElementChild as HTMLElement;
    const roundRectLighting = host.children[2].querySelector(
      '[data-pptx-shape3d-lighting="distance-field"]',
    ) as SVGGraphicsElement;
    const groupLighting = group.querySelector(
      '[data-pptx-shape3d-lighting="distance-field"]',
    ) as SVGGraphicsElement;
    const groupedDonutLighting = groupedDonut.querySelector(
      '[data-pptx-shape3d-lighting="distance-field"]',
    ) as SVGImageElement;
    const groupedDonutTexture = new Image();
    groupedDonutTexture.src = groupedDonutLighting.getAttribute('href')!;
    await groupedDonutTexture.decode();
    const ellipseLighting = host.children[3].querySelector(
      '[data-pptx-shape3d-lighting="distance-field"]',
    ) as SVGGraphicsElement;
    const donutLighting = donut.querySelector(
      '[data-pptx-shape3d-lighting="distance-field"]',
    ) as SVGGraphicsElement;
    const donutClipPath = donut.querySelector('clipPath path');
    return {
      bevelCount: host.querySelectorAll('[data-pptx-shape3d-bevel]').length,
      lightingCount: host.querySelectorAll('[data-pptx-shape3d-lighting="distance-field"]').length,
      flatHasBevel: !!flat.querySelector('[data-pptx-shape3d-bevel]'),
      uniqueIds: new Set(ids).size === ids.length,
      noHorizontalGrowth: host.scrollWidth === host.clientWidth,
      groupBounds: {
        width: group.getBoundingClientRect().width,
        height: group.getBoundingClientRect().height,
        childWidth: groupChild.getBoundingClientRect().width,
        childHeight: groupChild.getBoundingClientRect().height,
      },
      roundRectLightingBounds: {
        width: roundRectLighting.getBoundingClientRect().width,
        height: roundRectLighting.getBoundingClientRect().height,
      },
      ellipseLightingBounds: {
        width: ellipseLighting.getBoundingClientRect().width,
        height: ellipseLighting.getBoundingClientRect().height,
      },
      donutLightingBounds: {
        width: donutLighting.getBoundingClientRect().width,
        height: donutLighting.getBoundingClientRect().height,
      },
      donutClipSubpaths: donutClipPath?.getAttribute('d')?.match(/M/g)?.length ?? 0,
      donutClipFillRule: donutClipPath?.getAttribute('fill-rule'),
      groupLightingBounds: {
        width: groupLighting.getBoundingClientRect().width,
        height: groupLighting.getBoundingClientRect().height,
      },
      groupedDonutLightingBounds: {
        width: groupedDonutLighting.getBoundingClientRect().width,
        height: groupedDonutLighting.getBoundingClientRect().height,
      },
      groupedDonutTextureSize: {
        width: groupedDonutTexture.naturalWidth,
        height: groupedDonutTexture.naturalHeight,
      },
      disposableHadBevel,
      disposableHadLighting,
      croppedPictureBounds,
      revokedCount: revoked.length,
    };
  });

  expect(result).toEqual(
    expect.objectContaining({
      bevelCount: 8,
      lightingCount: 8,
      flatHasBevel: false,
      uniqueIds: true,
      noHorizontalGrowth: true,
      disposableHadBevel: true,
      disposableHadLighting: true,
      revokedCount: 2,
    }),
  );
  expect(result.groupBounds).toEqual({ width: 200, height: 100, childWidth: 100, childHeight: 50 });
  expect(result.roundRectLightingBounds).toEqual({ width: 220, height: 100 });
  expect(result.ellipseLightingBounds).toEqual({ width: 180, height: 120 });
  expect(result.donutLightingBounds).toEqual({ width: 180, height: 120 });
  expect(result.donutClipSubpaths).toBe(2);
  expect(result.donutClipFillRule).toBe('evenodd');
  expect(result.groupLightingBounds).toEqual({ width: 100, height: 50 });
  expect(result.groupedDonutLightingBounds).toEqual({ width: 100, height: 200 });
  expect(result.groupedDonutTextureSize).toEqual({ width: 400, height: 400 });
  expect(result.croppedPictureBounds).toEqual({
    x: expect.closeTo(-62.857, 2),
    y: expect.closeTo(-25.714, 2),
    width: expect.closeTo(285.714, 2),
    height: expect.closeTo(142.857, 2),
  });

  await expectStableScreenshot(page, page.locator('#shape3d-browser-host'));
});

test('bounded camera planes project in a browser and preserve text opt-out and group mapping', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { parseGroupNode } = await import('/src/model/nodes/GroupNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { renderGroup } = await import('/src/renderer/GroupRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const scene = `<a:scene3d>
      <a:camera prst="perspectiveRelaxedModerately" fov="7200000">
        <a:rot lat="18590633" lon="0" rev="0"/>
      </a:camera>
      <a:lightRig rig="threePt" dir="t"/>
    </a:scene3d>`;
    const shapeXml = (text = '', style = '') => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="1" name="3D camera plane"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="3840480" cy="3840480"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:solidFill><a:srgbClr val="2F75B5"/></a:solidFill>
          <a:ln><a:noFill/></a:ln>${scene}<a:sp3d extrusionH="0"/>
        </p:spPr>
        ${style}
        <p:txBody><a:bodyPr/><a:lstStyle/><a:p>${
          text ? `<a:r><a:t>${text}</a:t></a:r>` : ''
        }</a:p></p:txBody>
      </p:sp>`;
    const baseContext = createMockRenderContext();
    const presentation = {
      ...baseContext.presentation,
      width: 1280,
      height: 720,
    };
    const standalone = renderShape(
      parseShapeNode(parseXml(shapeXml())),
      createMockRenderContext({ presentation }),
    );
    const textOptOut = renderShape(
      parseShapeNode(parseXml(shapeXml('Readable'))),
      createMockRenderContext({ presentation }),
    );
    const shadowed = renderShape(
      parseShapeNode(
        parseXml(
          shapeXml(
            '',
            '<p:style><a:effectRef idx="2"><a:schemeClr val="accent1"/></a:effectRef></p:style>',
          ),
        ),
      ),
      createMockRenderContext({
        presentation,
        theme: {
          ...baseContext.theme,
          effectStyles: [
            parseXml(
              '<a:effectStyle xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:effectLst/></a:effectStyle>',
            ),
            parseXml(
              '<a:effectStyle xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:effectLst><a:outerShdw blurRad="40000" dist="23000" dir="5400000" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="35000"/></a:srgbClr></a:outerShdw></a:effectLst></a:effectStyle>',
            ),
          ],
        },
      }),
    );
    const groupXml = `
      <p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvGrpSpPr><p:cNvPr id="20" name="Camera group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
        <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="3657600" cy="2743200"/><a:chOff x="0" y="0"/><a:chExt cx="7315200" cy="3657600"/></a:xfrm></p:grpSpPr>
        ${shapeXml()}
      </p:grpSp>`;
    const group = renderGroup(
      parseGroupNode(parseXml(groupXml)),
      createMockRenderContext({ presentation }),
      (node, context) => renderShape(node as Parameters<typeof renderShape>[0], context),
    );
    document.body.append(standalone, textOptOut, shadowed, group);
    const projected = standalone.querySelector(
      '[data-pptx-shape3d-projected-plane="perspective"]',
    ) as SVGGraphicsElement;
    const projectedBounds = projected.getBBox();
    const gradient = standalone.querySelector('linearGradient');
    const shadowedProjected = shadowed.querySelector(
      '[data-pptx-shape3d-projected-plane="perspective"]',
    ) as SVGGraphicsElement;
    const shadowedBounds = shadowedProjected.getBBox();
    const shadowFilter = shadowed.querySelector('filter[id^="shape-shadow-"]')!;
    const shadowPrimitive = shadowFilter.querySelector('feDropShadow');
    const shadowFilterX = Number(shadowFilter.getAttribute('x'));
    const shadowFilterY = Number(shadowFilter.getAttribute('y'));
    const shadowFilterRight = shadowFilterX + Number(shadowFilter.getAttribute('width'));
    const shadowFilterBottom = shadowFilterY + Number(shadowFilter.getAttribute('height'));
    const groupChild = group.firstElementChild as HTMLElement;
    return {
      baseVisibility: standalone.querySelector('svg > path')?.getAttribute('visibility'),
      plainProjectedFilter: projected.getAttribute('filter'),
      projectedBounds: {
        x: projectedBounds.x,
        y: projectedBounds.y,
        width: projectedBounds.width,
        height: projectedBounds.height,
      },
      gradientInterpolation: gradient?.getAttribute('color-interpolation'),
      shadowedProjectedFilter: shadowedProjected.getAttribute('filter'),
      shadowedBaseFilter: shadowed.querySelector('svg > path')?.getAttribute('filter'),
      shadowStdDeviation: shadowPrimitive?.getAttribute('stdDeviation'),
      shadowDx: shadowPrimitive?.getAttribute('dx'),
      shadowDy: shadowPrimitive?.getAttribute('dy'),
      shadowColorInterpolation: shadowFilter.getAttribute('color-interpolation-filters'),
      shadowFilterCoversProjection:
        shadowFilterX < shadowedBounds.x &&
        shadowFilterY < shadowedBounds.y &&
        shadowFilterRight > shadowedBounds.x + shadowedBounds.width &&
        shadowFilterBottom > shadowedBounds.y + shadowedBounds.height,
      textOptOutProjected: !!textOptOut.querySelector('[data-pptx-shape3d-projected-plane]'),
      textOptOutBaseHidden: textOptOut.querySelector('svg > path')?.hasAttribute('visibility'),
      text: textOptOut.textContent,
      groupProjected: !!group.querySelector('[data-pptx-shape3d-projected-plane]'),
      groupChildSize: {
        width: groupChild.getBoundingClientRect().width,
        height: groupChild.getBoundingClientRect().height,
      },
    };
  });

  expect(result.baseVisibility).toBe('hidden');
  expect(result.plainProjectedFilter).toBeNull();
  expect(result.projectedBounds).toEqual({
    x: expect.closeTo(-157.6, 1),
    y: expect.closeTo(112.3, 1),
    width: expect.closeTo(718.3, 1),
    height: expect.closeTo(319.4, 1),
  });
  expect(result.gradientInterpolation).toBe('linearRGB');
  expect(result.shadowedProjectedFilter).toMatch(/^url\(#shape-shadow-/);
  expect(result.shadowedBaseFilter).toBeNull();
  expect(result.shadowStdDeviation).toBe('3.74');
  expect(result.shadowDx).toBe('0.0');
  expect(result.shadowDy).toBe('4.3');
  expect(result.shadowColorInterpolation).toBe('sRGB');
  expect(result.shadowFilterCoversProjection).toBe(true);
  expect(result.textOptOutProjected).toBe(false);
  expect(result.textOptOutBaseHidden).toBe(false);
  expect(result.text).toContain('Readable');
  expect(result.groupProjected).toBe(true);
  expect(result.groupChildSize).toEqual({
    width: expect.closeTo(201.6, 1),
    height: expect.closeTo(302.4, 1),
  });
});

test('perspective camera preserves a bounded multi-contour cubic custom silhouette', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const scene = `<a:scene3d>
      <a:camera prst="perspectiveRelaxedModerately" fov="7200000">
        <a:rot lat="18590633" lon="0" rev="0"/>
      </a:camera>
      <a:lightRig rig="threePt" dir="t"/>
    </a:scene3d>`;
    const geometry = (secondContour: boolean) => `<a:custGeom>
      <a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/><a:rect l="l" t="t" r="r" b="b"/>
      <a:pathLst><a:path w="1000" h="1000">
        <a:moveTo><a:pt x="0" y="500"/></a:moveTo>
        <a:cubicBezTo><a:pt x="0" y="120"/><a:pt x="280" y="0"/><a:pt x="450" y="160"/></a:cubicBezTo>
        <a:cubicBezTo><a:pt x="560" y="270"/><a:pt x="480" y="500"/><a:pt x="300" y="580"/></a:cubicBezTo>
        <a:lnTo><a:pt x="0" y="720"/></a:lnTo><a:close/>
        ${
          secondContour
            ? `<a:moveTo><a:pt x="560" y="180"/></a:moveTo>
               <a:cubicBezTo><a:pt x="700" y="20"/><a:pt x="1000" y="120"/><a:pt x="950" y="430"/></a:cubicBezTo>
               <a:cubicBezTo><a:pt x="920" y="680"/><a:pt x="680" y="900"/><a:pt x="520" y="720"/></a:cubicBezTo>
               <a:cubicBezTo><a:pt x="410" y="590"/><a:pt x="450" y="320"/><a:pt x="560" y="180"/></a:cubicBezTo><a:close/>`
            : ''
        }
      </a:path></a:pathLst>
    </a:custGeom>`;
    const shapeXml = (secondContour: boolean) => `<p:sp
      xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:nvSpPr><p:cNvPr id="1" name="Custom camera plane"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
      <p:spPr>
        <a:xfrm><a:off x="0" y="0"/><a:ext cx="3840480" cy="3840480"/></a:xfrm>
        ${geometry(secondContour)}
        <a:solidFill><a:srgbClr val="2F75B5"/></a:solidFill>
        <a:ln><a:noFill/></a:ln>${scene}
      </p:spPr>
    </p:sp>`;
    const baseContext = createMockRenderContext();
    const presentation = { ...baseContext.presentation, width: 1280, height: 720 };
    const supported = renderShape(
      parseShapeNode(parseXml(shapeXml(true))),
      createMockRenderContext({ presentation }),
    );
    const inverse = renderShape(
      parseShapeNode(parseXml(shapeXml(false))),
      createMockRenderContext({ presentation }),
    );
    const wrongCoordinateSpace = renderShape(
      parseShapeNode(parseXml(shapeXml(true).replace('w="1000" h="1000"', 'w="2000" h="1000"'))),
      createMockRenderContext({ presentation }),
    );
    const wrongTextRect = renderShape(
      parseShapeNode(
        parseXml(
          shapeXml(true).replace(
            '<a:rect l="l" t="t" r="r" b="b"/>',
            '<a:rect l="0" t="t" r="r" b="b"/>',
          ),
        ),
      ),
      createMockRenderContext({ presentation }),
    );
    document.body.append(supported, inverse, wrongCoordinateSpace, wrongTextRect);
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)),
    );
    const base = supported.querySelector('svg > path') as SVGPathElement;
    const projected = supported.querySelector(
      '[data-pptx-shape3d-projected-custom-plane="perspective"]',
    ) as SVGPathElement | null;
    const projectedBounds = projected?.getBoundingClientRect();
    return {
      baseVisibility: base.getAttribute('visibility'),
      projectedPath: projected?.getAttribute('d'),
      projectedTransform: projected?.style.transform,
      projectedBounds: projectedBounds
        ? { width: projectedBounds.width, height: projectedBounds.height }
        : null,
      inverseProjected: !!inverse.querySelector('[data-pptx-shape3d-projected-custom-plane]'),
      inverseBaseVisibility: inverse.querySelector('svg > path')?.getAttribute('visibility'),
      wrongCoordinateSpaceProjected: !!wrongCoordinateSpace.querySelector(
        '[data-pptx-shape3d-projected-custom-plane]',
      ),
      wrongTextRectProjected: !!wrongTextRect.querySelector(
        '[data-pptx-shape3d-projected-custom-plane]',
      ),
    };
  });

  expect(result.baseVisibility).toBe('hidden');
  expect(result.projectedPath?.match(/M/g)).toHaveLength(2);
  expect(result.projectedPath?.match(/Z/g)).toHaveLength(2);
  expect(result.projectedPath).not.toContain('C');
  expect(result.projectedPath?.match(/L/g)?.length).toBeGreaterThan(16);
  expect(result.projectedTransform).toBe('');
  expect(result.projectedBounds?.width).toBeGreaterThan(300);
  expect(result.projectedBounds?.height).toBeGreaterThan(100);
  expect(result.inverseProjected).toBe(false);
  expect(result.inverseBaseVisibility).toBeNull();
  expect(result.wrongCoordinateSpaceProjected).toBe(false);
  expect(result.wrongTextRectProjected).toBe(false);
});

test('bottom-bevel front material stays bounded to opaque standalone slide shapes', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { parseGroupNode } = await import('/src/model/nodes/GroupNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { renderGroup } = await import('/src/renderer/GroupRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const shapeXml = ({ alpha = false, placeholder = false, text = '' } = {}) => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="31" name="Bottom bevel"/><p:cNvSpPr/><p:nvPr>${
          placeholder ? '<p:ph type="body"/>' : ''
        }</p:nvPr></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="5486400" cy="2743200"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:solidFill><a:srgbClr val="${alpha ? 'BDC4F0' : '4472C4'}">${
            alpha ? '<a:alpha val="5000"/>' : ''
          }</a:srgbClr></a:solidFill>
          <a:ln><a:noFill/></a:ln>
          <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"><a:rot lat="0" lon="0" rev="3000000"/></a:lightRig></a:scene3d>
          <a:sp3d prstMaterial="dkEdge"><a:bevelB prst="relaxedInset"/></a:sp3d>
        </p:spPr>
        <p:txBody><a:bodyPr anchor="ctr"/><a:lstStyle/><a:p><a:pPr algn="ctr"/>${
          text
            ? `<a:r><a:rPr sz="2000" b="1"><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:rPr><a:t>${text}</a:t></a:r>`
            : ''
        }</a:p></p:txBody>
      </p:sp>`;
    const host = document.createElement('div');
    host.id = 'bottom-bevel-front-host';
    host.style.position = 'relative';
    host.style.width = '1280px';
    host.style.height = '720px';
    const opaque = renderShape(
      parseShapeNode(parseXml(shapeXml({ text: '底部斜面' }))),
      createMockRenderContext(),
    );
    const transparent = renderShape(
      parseShapeNode(parseXml(shapeXml({ alpha: true, text: '运营管理' }))),
      createMockRenderContext(),
    );
    transparent.style.top = '320px';
    const placeholder = renderShape(
      parseShapeNode(parseXml(shapeXml({ placeholder: true }))),
      createMockRenderContext(),
    );
    placeholder.style.left = '620px';
    const groupXml = `
      <p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvGrpSpPr><p:cNvPr id="30" name="Bottom group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
        <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5486400" cy="2743200"/><a:chOff x="0" y="0"/><a:chExt cx="5486400" cy="2743200"/></a:xfrm></p:grpSpPr>
        ${shapeXml()}
      </p:grpSp>`;
    const grouped = renderGroup(
      parseGroupNode(parseXml(groupXml)),
      createMockRenderContext(),
      (node, context) => renderShape(node as Parameters<typeof renderShape>[0], context),
    );
    grouped.style.left = '620px';
    grouped.style.top = '320px';
    host.append(opaque, transparent, placeholder, grouped);
    document.body.append(host);
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)),
    );
    return {
      opaqueFill: opaque
        .querySelector('[data-pptx-shape3d-projected-plane="orthographic"]')
        ?.getAttribute('fill'),
      opaqueMaterial: !!opaque.querySelector('[data-pptx-shape3d-front-material="dkEdge"]'),
      opaqueText: opaque.textContent,
      transparentProjected: !!transparent.querySelector('[data-pptx-shape3d-projected-plane]'),
      transparentFill: transparent.querySelector('svg > path')?.getAttribute('fill'),
      placeholderProjected: !!placeholder.querySelector('[data-pptx-shape3d-projected-plane]'),
      groupProjected: !!grouped.querySelector('[data-pptx-shape3d-projected-plane]'),
    };
  });

  expect(result).toEqual({
    opaqueFill: '#4676cb',
    opaqueMaterial: true,
    opaqueText: expect.stringContaining('底部斜面'),
    transparentProjected: false,
    transparentFill: 'rgba(189,196,240,0.050)',
    placeholderProjected: false,
    groupProjected: false,
  });
  await expectStableScreenshot(page, page.locator('#bottom-bevel-front-host'));
});

test('scene-only camera projection preserves live text and rejects styled text planes', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const shapeXml = (styled: boolean) => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="1" name="Scene-only camera text"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="4206240" cy="3657600"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:noFill/>
          <a:scene3d>
            <a:camera prst="perspectiveContrastingRightFacing" fov="5100000">
              <a:rot lat="0" lon="19532225" rev="0"/>
            </a:camera>
            <a:lightRig rig="threePt" dir="t"/>
          </a:scene3d>
        </p:spPr>
        ${styled ? '<p:style><a:fontRef idx="minor"><a:schemeClr val="tx1"/></a:fontRef></p:style>' : ''}
        <p:txBody>
          <a:bodyPr wrap="none" anchor="ctr"><a:spAutoFit/></a:bodyPr>
          <a:lstStyle/>
          <a:p><a:pPr algn="ctr"/><a:r><a:rPr sz="2600"/><a:t>Editable camera text</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>`;
    const presentation = {
      ...createMockRenderContext().presentation,
      width: 1280,
      height: 720,
    };
    document.body.style.margin = '0';
    const projectedShape = renderShape(
      parseShapeNode(parseXml(shapeXml(false))),
      createMockRenderContext({ presentation }),
    );
    const styledShape = renderShape(
      parseShapeNode(parseXml(shapeXml(true))),
      createMockRenderContext({ presentation }),
    );
    styledShape.style.left = '600px';
    document.body.append(projectedShape, styledShape);
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    const projected = projectedShape.querySelector<HTMLElement>(
      '[data-pptx-shape3d-projected-text-plane="perspective"]',
    );
    const styled = styledShape.querySelector<HTMLElement>(
      '[data-pptx-shape3d-projected-text-plane]',
    );
    const bounds = projected?.getBoundingClientRect();
    return {
      projected: !!projected,
      transform: projected ? getComputedStyle(projected).transform : '',
      liveText: projected?.textContent,
      hasRasterReplacement: !!projected?.querySelector('canvas, img, svg'),
      bounds: bounds
        ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }
        : null,
      styledProjected: !!styled,
      styledText: styledShape.textContent,
    };
  });

  expect(result.projected).toBe(true);
  expect(result.transform).toMatch(/^matrix3d\(/);
  expect(result.liveText).toContain('Editable camera text');
  expect(result.hasRasterReplacement).toBe(false);
  expect(result.bounds).toEqual({
    x: expect.closeTo(-3.5, 1),
    y: expect.closeTo(-44.5, 1),
    width: expect.closeTo(377.3, 0),
    height: expect.closeTo(473.1, 0),
  });
  expect(result.styledProjected).toBe(false);
  expect(result.styledText).toContain('Editable camera text');
});

test('perspective-left preset projects top-anchored live text and rejects explicit rotation', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const shapeXml = (explicitRotation: boolean) => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="1" name="Perspective-left text"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="3840480" cy="3840480"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/>
          <a:scene3d>
            <a:camera prst="perspectiveLeft" fov="7200000">
              ${explicitRotation ? '<a:rot lat="0" lon="1200000" rev="0"/>' : ''}
            </a:camera>
            <a:lightRig rig="threePt" dir="t"/>
          </a:scene3d>
        </p:spPr>
        <p:txBody><a:bodyPr wrap="none"><a:spAutoFit/></a:bodyPr><a:lstStyle/>
          <a:p><a:r><a:rPr sz="2800"/><a:t>透视文本 LEFT 120</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>`;
    const presentation = {
      ...createMockRenderContext().presentation,
      width: 1280,
      height: 720,
    };
    const projectedShape = renderShape(
      parseShapeNode(parseXml(shapeXml(false))),
      createMockRenderContext({ presentation }),
    );
    const explicitRotationShape = renderShape(
      parseShapeNode(parseXml(shapeXml(true))),
      createMockRenderContext({ presentation }),
    );
    explicitRotationShape.style.left = '600px';
    document.body.append(projectedShape, explicitRotationShape);
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    const projected = projectedShape.querySelector<HTMLElement>(
      '[data-pptx-shape3d-projected-text-plane="perspective"]',
    );
    return {
      projected: !!projected,
      transform: projected ? getComputedStyle(projected).transform : '',
      text: projected?.textContent,
      rasterized: !!projected?.querySelector('canvas, img, svg'),
      explicitRotationProjected: !!explicitRotationShape.querySelector(
        '[data-pptx-shape3d-projected-text-plane]',
      ),
    };
  });

  expect(result.projected).toBe(true);
  expect(result.transform).toMatch(/^matrix3d\(/);
  expect(result.text).toContain('透视文本 LEFT 120');
  expect(result.rasterized).toBe(false);
  expect(result.explicitRotationProjected).toBe(false);
});

test('perspective-right picture projection preserves live image crop and bounded opt-outs', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parsePicNode } = await import('/src/model/nodes/PicNode.ts');
    const { renderImage } = await import('/src/renderer/ImageRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const pictureXml = (explicitRotation: boolean, fillRect: boolean) => `
      <p:pic xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <p:nvPicPr><p:cNvPr id="1" name="Perspective-right picture"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="rId1"/><a:srcRect l="22000" t="18000" r="8000" b="12000"/>
          <a:stretch>${fillRect ? '<a:fillRect/>' : ''}</a:stretch>
        </p:blipFill>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="3840480" cy="3840480"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:scene3d><a:camera prst="perspectiveRight" fov="5700000">
            ${explicitRotation ? '<a:rot lat="0" lon="-1200000" rev="0"/>' : ''}
          </a:camera><a:lightRig rig="threePt" dir="t"/></a:scene3d>
        </p:spPr>
      </p:pic>`;
    const png = Uint8Array.from(
      atob(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z2S8AAAAASUVORK5CYII=',
      ),
      (char) => char.charCodeAt(0),
    );
    const presentation = {
      ...createMockRenderContext().presentation,
      width: 1280,
      height: 720,
    };
    const render = (explicitRotation: boolean, fillRect: boolean) => {
      const context = createMockRenderContext({ presentation });
      context.slide.rels.set('rId1', { type: 'image', target: 'ppt/media/image1.png' });
      context.presentation.media.set('ppt/media/image1.png', png);
      return renderImage(parsePicNode(parseXml(pictureXml(explicitRotation, fillRect))), context);
    };

    document.body.style.margin = '0';
    const projectedPicture = render(false, false);
    const explicitRotationPicture = render(true, false);
    const fillRectPicture = render(false, true);
    explicitRotationPicture.style.left = '600px';
    fillRectPicture.style.left = '1000px';
    document.body.append(projectedPicture, explicitRotationPicture, fillRectPicture);
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );

    const stage = projectedPicture.querySelector<HTMLElement>(
      '[data-pptx-shape3d-projected-picture-plane="perspective"]',
    );
    const image = stage?.querySelector<HTMLImageElement>('img');
    const bounds = stage?.getBoundingClientRect();
    return {
      projected: !!stage,
      transform: stage ? getComputedStyle(stage).transform : '',
      wrapperOverflow: projectedPicture.style.overflow,
      stageOverflow: stage?.style.overflow,
      liveImage: !!image,
      imageFilter: image?.style.filter,
      lighting: stage?.querySelector<HTMLElement>(
        '[data-pptx-shape3d-picture-lighting="threePt:t"]',
      )?.style.backgroundColor,
      crop: image
        ? {
            width: image.style.width,
            height: image.style.height,
            marginLeft: image.style.marginLeft,
            marginTop: image.style.marginTop,
          }
        : null,
      bounds: bounds
        ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }
        : null,
      explicitRotationProjected: !!explicitRotationPicture.querySelector(
        '[data-pptx-shape3d-projected-picture-plane]',
      ),
      fillRectProjected: !!fillRectPicture.querySelector(
        '[data-pptx-shape3d-projected-picture-plane]',
      ),
    };
  });

  expect(result.projected).toBe(true);
  expect(result.transform).toMatch(/^matrix3d\(/);
  expect(result.wrapperOverflow).toBe('visible');
  expect(result.stageOverflow).toBe('hidden');
  expect(result.liveImage).toBe(true);
  expect(result.imageFilter).toBe('brightness(1.01)');
  expect(result.lighting).toBe('rgba(255, 255, 255, 0.09)');
  expect(result.crop).toEqual({
    width: '576px',
    height: '576px',
    marginLeft: '-126.72px',
    marginTop: '-103.68px',
  });
  expect(result.bounds).toEqual({
    x: expect.closeTo(-14.6, 1),
    y: expect.closeTo(-28.5, 1),
    width: expect.closeTo(384.8, 1),
    height: expect.closeTo(460.1, 1),
  });
  expect(result.explicitRotationProjected).toBe(false);
  expect(result.fillRectProjected).toBe(false);
});

test('perspective-left group projection keeps live picture children and completed reflections', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseGroupNode } = await import('/src/model/nodes/GroupNode.ts');
    const { renderGroup } = await import('/src/renderer/GroupRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const picture = (id: number, top: number) => `
      <p:pic>
        <p:nvPicPr><p:cNvPr id="${id}" name="Picture ${id}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="rId${id}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
        <p:spPr><a:xfrm><a:off x="0" y="${top}"/><a:ext cx="1905000" cy="476250"/></a:xfrm></p:spPr>
      </p:pic>`;
    const groupXml = (fieldOfView: number) => `
      <p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
               xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <p:nvGrpSpPr><p:cNvPr id="10" name="Picture camera group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
        <p:grpSpPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/>
            <a:chOff x="0" y="0"/><a:chExt cx="1905000" cy="952500"/>
          </a:xfrm>
          <a:scene3d><a:camera prst="perspectiveLeft" fov="${fieldOfView}">
            <a:rot lat="0" lon="1500000" rev="0"/>
          </a:camera><a:lightRig rig="threePt" dir="t"/></a:scene3d>
        </p:grpSpPr>
        ${picture(11, 0)}${picture(12, 476250)}
      </p:grpSp>`;
    const renderNode = (node: {
      id: string;
      position: { x: number; y: number };
      size: { w: number; h: number };
    }) => {
      const element = document.createElement('div');
      element.dataset.nodeId = node.id;
      element.style.position = 'absolute';
      element.style.left = `${node.position.x}px`;
      element.style.top = `${node.position.y}px`;
      element.style.width = `${node.size.w}px`;
      element.style.height = `${node.size.h}px`;
      element.style.background = node.id === '11' ? '#4472c4' : '#70ad47';
      return element;
    };
    const basePresentation = createMockRenderContext().presentation;
    const context = createMockRenderContext({
      groupDepth: 3,
      groupTransformHasRotationOrFlip: false,
      presentation: { ...basePresentation, width: 1280, height: 720 },
    });
    const supported = renderGroup(parseGroupNode(parseXml(groupXml(5700000))), context, renderNode);
    const inverse = renderGroup(parseGroupNode(parseXml(groupXml(5760000))), context, renderNode);
    supported.id = 'group-camera-supported';
    inverse.style.left = '500px';
    document.body.append(supported, inverse);

    const reflected = renderGroup(
      parseGroupNode(
        parseXml(`
          <p:grpSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
            <p:nvGrpSpPr><p:cNvPr id="20" name="Reflective parent"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
            <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/>
              <a:chOff x="0" y="0"/><a:chExt cx="1905000" cy="952500"/>
            </a:xfrm><a:effectLst><a:reflection/></a:effectLst></p:grpSpPr>
            <p:sp><p:nvSpPr><p:cNvPr id="21" name="Child"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
              <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/></a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
            </p:sp>
          </p:grpSp>`),
      ),
      createMockRenderContext(),
      renderNode,
    );
    document.body.append(reflected);
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    const layer = supported.querySelector<HTMLElement>(
      '[data-pptx-shape3d-projected-group-plane="perspective"]',
    );
    const content = layer?.querySelector<HTMLElement>('[data-pptx-shape3d-group-content]');
    const bounds = layer?.getBoundingClientRect();
    return {
      transform: layer ? getComputedStyle(layer).transform : '',
      liveChildCount: content?.children.length,
      lighting: !!layer?.querySelector('[data-pptx-shape3d-group-lighting]'),
      contentFilter: content ? getComputedStyle(content).filter : '',
      bounds: bounds
        ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }
        : null,
      inverseProjected: !!inverse.querySelector('[data-pptx-shape3d-projected-group-plane]'),
      inverseFallback: inverse.dataset.pptxShape3dFallback,
      reflectedChild: !!reflected.querySelector(
        '[data-pptx-reflection-source="true"] [data-node-id="21"]',
      ),
    };
  });

  expect(result.transform).toMatch(/^matrix3d\(/);
  expect(result.liveChildCount).toBe(2);
  expect(result.lighting).toBe(true);
  expect(result.contentFilter).toMatch(/^brightness\(/);
  expect(result.bounds?.width).toBeGreaterThan(150);
  expect(result.bounds?.height).toBeGreaterThan(90);
  expect(result.inverseProjected).toBe(false);
  expect(result.inverseFallback).toBe('camera-field-of-view');
  expect(result.reflectedChild).toBe(true);
});

test('static 3D donut composes with its upper adjustment bound and a solid theme fill', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const scene =
      '<a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>';
    const bevel = '<a:sp3d><a:bevelT w="127000" h="127000" prst="circle"/></a:sp3d>';
    const xml = (adjustment: number, themeFill: boolean) => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="1" name="3D donut"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="3840480" cy="3840480"/></a:xfrm>
          <a:prstGeom prst="donut"><a:avLst><a:gd name="adj" fmla="val ${adjustment}"/></a:avLst></a:prstGeom>
          ${themeFill ? '' : '<a:solidFill><a:srgbClr val="2F75B5"/></a:solidFill>'}
          ${scene}${bevel}
        </p:spPr>
        ${
          themeFill
            ? '<p:style><a:lnRef idx="0"><a:schemeClr val="accent1"/></a:lnRef><a:fillRef idx="1"><a:schemeClr val="accent1"/></a:fillRef><a:effectRef idx="0"><a:schemeClr val="accent1"/></a:effectRef><a:fontRef idx="minor"><a:schemeClr val="tx1"/></a:fontRef></p:style>'
            : ''
        }
      </p:sp>`;

    const tasks: Promise<void>[] = [];
    const upper = renderShape(
      parseShapeNode(parseXml(xml(50000, false))),
      createMockRenderContext({ asyncTasks: tasks }),
    );
    const themed = renderShape(
      parseShapeNode(parseXml(xml(32000, true))),
      createMockRenderContext({ asyncTasks: tasks }),
    );
    document.body.append(upper, themed);
    await Promise.all(tasks);

    return {
      upperPath: upper.querySelector('svg > path')?.getAttribute('d'),
      upperLighting: !!upper.querySelector('[data-pptx-shape3d-lighting="distance-field"]'),
      themedFill: themed.querySelector('svg > path')?.getAttribute('fill'),
      themedLighting: !!themed.querySelector('[data-pptx-shape3d-lighting="distance-field"]'),
    };
  });

  expect(result.upperPath).toBeTruthy();
  expect(result.upperPath).not.toMatch(/NaN|Infinity/);
  expect(result.upperLighting).toBe(true);
  expect(result.themedFill).toBe('#4472C4');
  expect(result.themedLighting).toBe(true);
});

test('orthographic circle bevel uses PowerPoint-like face lighting instead of a flat border', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const xml = `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="1" name="3D face lighting"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:solidFill><a:srgbClr val="2F75B5"/></a:solidFill>
          <a:ln><a:noFill/></a:ln>
          <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
          <a:sp3d><a:bevelT w="127000" h="127000" prst="circle"/></a:sp3d>
        </p:spPr>
      </p:sp>`;
    document.body.style.margin = '0';
    const ctx = createMockRenderContext({ asyncTasks: [] });
    const rendered = renderShape(parseShapeNode(parseXml(xml)), ctx);
    rendered.id = 'shape3d-face-lighting';
    document.body.append(rendered);
    await Promise.all(ctx.asyncTasks!);
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });

  const png = PNG.sync.read(await page.locator('#shape3d-face-lighting').screenshot());
  const rgb = (x: number, y: number): [number, number, number] => {
    const offset = (y * png.width + x) * 4;
    return [png.data[offset], png.data[offset + 1], png.data[offset + 2]];
  };
  const luminance = ([r, g, b]: [number, number, number]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const center = rgb(100, 50);
  const topSpecular = rgb(100, 7);

  expect(luminance(topSpecular)).toBeGreaterThan(luminance(center) + 30);
  expect(topSpecular[2] - topSpecular[0]).toBeGreaterThan(100);
  expect(luminance(rgb(4, 50))).toBeLessThan(luminance(center));
  // Keep this browser check focused on directional face ordering. The native
  // bevel-local matrix owns the tighter PowerPoint dark-face amplitude bounds.
  expect(luminance(rgb(195, 50))).toBeLessThan(luminance(center) - 18);
  expect(luminance(rgb(100, 95))).toBeLessThan(luminance(center) - 20);
  for (const [inner, face] of rgb(20, 50).map((channel, index) => [channel, center[index]])) {
    expect(Math.abs(inner - face)).toBeLessThanOrEqual(5);
  }
});

test('paragraph defRPr and shape fontRef keep DrawingML color precedence in a browser', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const colors = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const specimens = [
      {
        name: 'paragraph-srgb',
        paragraphFill: '<a:solidFill><a:srgbClr val="C00000"/></a:solidFill>',
        runFill: '',
      },
      {
        name: 'paragraph-scheme',
        paragraphFill: '<a:solidFill><a:schemeClr val="accent2"/></a:solidFill>',
        runFill: '',
      },
      {
        name: 'explicit-run',
        paragraphFill: '<a:solidFill><a:schemeClr val="accent2"/></a:solidFill>',
        runFill: '<a:solidFill><a:srgbClr val="7030A0"/></a:solidFill>',
      },
      { name: 'fontref-fallback', paragraphFill: '', runFill: '' },
    ];

    return specimens.map(({ name, paragraphFill, runFill }) => {
      const xml = `
        <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <p:nvSpPr><p:cNvPr id="1" name="${name}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
          <p:spPr>
            <a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/></a:xfrm>
            <a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/>
          </p:spPr>
          <p:style>
            <a:lnRef idx="0"><a:schemeClr val="accent1"/></a:lnRef>
            <a:fillRef idx="0"><a:schemeClr val="accent1"/></a:fillRef>
            <a:effectRef idx="0"><a:schemeClr val="accent1"/></a:effectRef>
            <a:fontRef idx="minor"><a:schemeClr val="accent1"/></a:fontRef>
          </p:style>
          <p:txBody><a:bodyPr/><a:lstStyle/><a:p>
            <a:pPr><a:defRPr>${paragraphFill}</a:defRPr></a:pPr>
            <a:r><a:rPr lang="zh-CN">${runFill}</a:rPr><a:t>${name}</a:t></a:r>
          </a:p></p:txBody>
        </p:sp>`;
      const rendered = renderShape(parseShapeNode(parseXml(xml)), createMockRenderContext());
      document.body.append(rendered);
      return getComputedStyle(rendered.querySelector('span')!).color;
    });
  });

  expect(colors).toEqual([
    'rgb(192, 0, 0)',
    'rgb(237, 125, 49)',
    'rgb(112, 48, 160)',
    'rgb(68, 114, 196)',
  ]);
});

for (const hostWhiteSpace of ['normal', 'pre', 'nowrap']) {
  for (const wrap of ['square', 'none']) {
    test(`text wrap=${wrap} inside white-space:${hostWhiteSpace}`, async ({ page }) => {
      await page.goto('/test/browser/blank.html');
      const result = await page.evaluate(
        async ({ hostWhiteSpace, wrap }) => {
          const { renderTextFixture } = await import('/test/fixtures/text-coverage.ts');
          document.body.style.whiteSpace = hostWhiteSpace;
          const { element, span, para, container } = renderTextFixture(
            `<bodyPr wrap="${wrap}" lIns="0" tIns="0" rIns="0" bIns="0"><noAutofit/></bodyPr>`,
          );
          document.body.append(element);
          await document.fonts.ready;
          const range = document.createRange();
          range.selectNodeContents(span);
          return {
            whiteSpace: getComputedStyle(container).whiteSpace,
            lineCount: range.getClientRects().length,
            width: element.getBoundingClientRect().width,
            paragraphWidth: para.getBoundingClientRect().width,
            scale: container.style.transform,
            fontSize: getComputedStyle(span).fontSize,
          };
        },
        { hostWhiteSpace, wrap },
      );
      expect(result.whiteSpace).toBe(wrap === 'none' ? 'nowrap' : 'normal');
      expect(result.fontSize).toBe('32px');
      expect(result.scale).toBe('');
      expect(result.paragraphWidth).toBeLessThanOrEqual(result.width);
      if (wrap === 'none') expect(result.lineCount).toBe(1);
      else expect(result.lineCount).toBeGreaterThan(1);
    });
  }
}

test('spAutoFit grows a wrapped CJK text box without shrinking glyphs or reflowing siblings', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { renderTextFixture } = await import('/test/fixtures/text-coverage.ts');
    const { element, span, container } = renderTextFixture(
      '<bodyPr wrap="square" lIns="109728" rIns="109728" tIns="73152" bIns="73152"><spAutoFit/></bodyPr>',
      '',
      '',
      '',
      '<a:r><a:rPr sz="3000"><a:latin typeface="Microsoft YaHei"/><a:ea typeface="Microsoft YaHei"/></a:rPr><a:t>Alpha 坚守问题导向，持续提升复杂演示文稿的渲染质量与一致性。</a:t></a:r>',
    );
    const parent = document.createElement('div');
    Object.assign(parent.style, {
      position: 'relative',
      width: '500px',
      height: '300px',
    });
    const sibling = document.createElement('div');
    Object.assign(sibling.style, {
      position: 'absolute',
      left: '0',
      top: '140px',
      width: '20px',
      height: '20px',
    });
    parent.append(element, sibling);
    document.body.append(parent);
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    const range = document.createRange();
    range.selectNodeContents(span);
    return {
      fontSize: getComputedStyle(span).fontSize,
      lineCount: range.getClientRects().length,
      transform: container.style.transform,
      overflowY: getComputedStyle(container).overflowY,
      shapeHeight: element.getBoundingClientRect().height,
      textHeight: container.getBoundingClientRect().height,
      parentHeight: parent.getBoundingClientRect().height,
      siblingTop: sibling.offsetTop,
    };
  });

  expect(result.fontSize).toBe('40px');
  expect(result.lineCount).toBeGreaterThan(2);
  expect(result.transform).toBe('');
  expect(result.overflowY).toBe('visible');
  expect(result.shapeHeight).toBeGreaterThan(80);
  expect(result.textHeight).toBeCloseTo(result.shapeHeight, 1);
  expect(result.parentHeight).toBe(300);
  expect(result.siblingTop).toBe(140);
});

test('spAutoFit grows a compact wide text box when its run preserves an explicit font size', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { renderTextFixture } = await import('/test/fixtures/text-coverage.ts');
    const { element, span, container } = renderTextFixture(
      '<bodyPr wrap="square"><spAutoFit/></bodyPr>',
      '',
      '',
      '',
      '<a:r><a:rPr sz="3000"><a:latin typeface="Microsoft YaHei"/><a:ea typeface="Microsoft YaHei"/></a:rPr><a:t>Alpha 坚守问题导向，持续提升复杂演示文稿的渲染质量与一致性。</a:t></a:r>',
      undefined,
      { cx: 7680960, cy: 411480 },
    );
    const centered = renderTextFixture(
      '<bodyPr wrap="square" anchor="ctr"><spAutoFit/></bodyPr>',
      '',
      '',
      '',
      '<a:r><a:rPr sz="3000"><a:latin typeface="Microsoft YaHei"/><a:ea typeface="Microsoft YaHei"/></a:rPr><a:t>Alpha 坚守问题导向，持续提升复杂演示文稿的渲染质量与一致性。</a:t></a:r>',
      undefined,
      { cx: 7680960, cy: 411480 },
    );
    document.body.append(element, centered.element);
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    const range = document.createRange();
    range.selectNodeContents(span);
    return {
      fontSize: getComputedStyle(span).fontSize,
      lineCount: range.getClientRects().length,
      transform: container.style.transform,
      overflowY: getComputedStyle(container).overflowY,
      shapeWidth: element.getBoundingClientRect().width,
      shapeHeight: element.getBoundingClientRect().height,
      centeredTransform: centered.container.style.transform,
      centeredShapeHeight: centered.element.getBoundingClientRect().height,
    };
  });

  expect(result.fontSize).toBe('40px');
  expect(result.lineCount).toBeGreaterThan(1);
  expect(result.transform).toBe('');
  expect(result.overflowY).toBe('visible');
  expect(result.shapeWidth).toBeCloseTo(806.4, 1);
  expect(result.shapeHeight).toBeGreaterThan(43.2);
  expect(result.centeredTransform).toContain('scale(');
  expect(result.centeredShapeHeight).toBeCloseTo(43.2, 1);
});

for (const [x, y] of [
  ['clip', 'clip'],
  ['clip', 'overflow'],
  ['overflow', 'clip'],
  ['overflow', 'overflow'],
]) {
  test(`noAutofit clip hit-testing and parent geometry ${x}/${y}`, async ({ page }) => {
    await page.goto('/test/browser/blank.html');
    const result = await page.evaluate(
      async ({ x, y }) => {
        const { renderTextFixture } = await import('/test/fixtures/text-coverage.ts');
        const { element, container } = renderTextFixture(
          `<bodyPr wrap="none" horzOverflow="${x}" vertOverflow="${y}" lIns="0" tIns="0" rIns="0" bIns="0"><noAutofit/></bodyPr>`,
          '',
          '',
          '',
          '<a:r><a:rPr sz="2400"/><a:t>Alpha beta gamma delta epsilon</a:t></a:r><a:br/><a:r><a:rPr sz="2400"/><a:t>Second</a:t></a:r><a:br/><a:r><a:rPr sz="2400"/><a:t>Third</a:t></a:r><a:br/><a:r><a:rPr sz="2400"/><a:t>Fourth</a:t></a:r>',
        );
        const parent = document.createElement('div');
        Object.assign(parent.style, {
          position: 'relative',
          width: '500px',
          height: '300px',
          margin: '40px',
          whiteSpace: 'pre',
        });
        parent.append(element);
        document.body.append(parent);
        await document.fonts.ready;
        const rect = container.getBoundingClientRect();
        const hit = (px: number, py: number) =>
          container.contains(document.elementFromPoint(px, py));
        return {
          overflowX: getComputedStyle(container).overflowX,
          overflowY: getComputedStyle(container).overflowY,
          horizontalHit: hit(rect.right + 15, rect.top + 15),
          verticalHit: hit(rect.left + 15, rect.bottom + 25),
          shapeHeight: element.getBoundingClientRect().height,
          parentWidth: parent.getBoundingClientRect().width,
        };
      },
      { x, y },
    );
    expect(result.overflowX).toBe(x === 'clip' ? 'clip' : 'visible');
    expect(result.overflowY).toBe(y === 'clip' ? 'clip' : 'visible');
    expect(result.horizontalHit).toBe(x === 'overflow');
    expect(result.verticalHit).toBe(y === 'overflow');
    expect(result.shapeHeight).toBe(80);
    expect(result.parentWidth).toBe(500);
  });
}

for (const [own, inherited, expectedSize] of [
  ['<noAutofit/>', '<normAutofit fontScale="50000"/>', '32px'],
  ['<normAutofit fontScale="50%"/>', '<noAutofit/>', '16px'],
  ['<spAutoFit/>', '<normAutofit fontScale="50000"/>', '32px'],
  ['', '<normAutofit fontScale="50000"/>', '16px'],
]) {
  test(`autofit choice ${own || 'inherited'} supersedes ${inherited}`, async ({ page }) => {
    await page.goto('/test/browser/blank.html');
    const result = await page.evaluate(
      async ({ own, inherited }) => {
        const { renderTextFixture } = await import('/test/fixtures/text-coverage.ts');
        const { element, span, container } = renderTextFixture(
          `<bodyPr wrap="square" horzOverflow="overflow" vertOverflow="overflow" lIns="0" tIns="0" rIns="0" bIns="0">${own}</bodyPr>`,
          '',
          '',
          '',
          '<a:r><a:rPr sz="2400"/><a:t>Alpha</a:t></a:r>',
          `<bodyPr>${inherited}</bodyPr>`,
        );
        document.body.append(element);
        await document.fonts.ready;
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        );
        return {
          fontSize: getComputedStyle(span).fontSize,
          width: element.getBoundingClientRect().width,
          transform: container.style.transform,
        };
      },
      { own, inherited },
    );
    expect(result.fontSize).toBe(expectedSize);
    expect(result.width).toBe(160);
    expect(result.transform).toBe('');
  });
}

test('vertical text, adjacent runs, bullets and multiple paragraphs preserve container bounds', async ({
  page,
}) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { renderTextFixture } = await import('/test/fixtures/text-coverage.ts');
    const { element, node, ctx } = renderTextFixture(
      '<bodyPr vert="eaVert" wrap="square" lIns="0" tIns="0" rIns="0" bIns="0"><noAutofit/></bodyPr>',
      '<buAutoNum type="arabicPeriod"/>',
      '<lvl1pPr><buChar char="•"/></lvl1pPr>',
      '',
      '<a:r><a:rPr sz="1200"/><a:t>Alpha</a:t></a:r><a:r><a:rPr sz="1200"/><a:t>乙</a:t></a:r>',
    );
    node.textBody!.paragraphs.push({ ...node.textBody!.paragraphs[0], runs: [{ text: 'Second' }] });
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const rendered = renderShape(node, ctx);
    element.remove();
    document.body.style.whiteSpace = 'pre';
    document.body.append(rendered);
    await document.fonts.ready;
    const span = [...rendered.querySelectorAll('span')].find((s) => s.textContent === 'Alpha')!;
    const container = span.closest('div')!.parentElement!;
    return {
      text: rendered.textContent,
      writingMode: getComputedStyle(container).writingMode,
      width: rendered.getBoundingClientRect().width,
      whiteSpace: getComputedStyle(container).whiteSpace,
    };
  });
  expect(result.text).toContain('1.');
  expect(result.text).toContain('2.');
  expect(result.text).not.toContain('•');
  expect(result.writingMode).toBe('vertical-rl');
  expect(result.whiteSpace).toBe('normal');
  expect(result.width).toBe(160);
});

test('near-fit square-wrapped heading stays on one line with bounded scale', async ({ page }) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderShape } = await import('/src/renderer/ShapeRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const text = '发扬遵义会议精神自觉做到 “两个维护”';
    const shapeXml = (wrap: string, noAutofit: boolean) => `
      <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:nvSpPr><p:cNvPr id="248" name="Near-fit heading"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="0" y="0"/><a:ext cx="28575000" cy="1905000"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/>
        </p:spPr>
        <p:txBody>
          <a:bodyPr wrap="${wrap}" lIns="0" tIns="0" rIns="0" bIns="0">${noAutofit ? '<a:noAutofit/>' : ''}</a:bodyPr>
          <a:lstStyle/>
          <a:p>
            <a:r><a:rPr sz="5400" b="1" spc="50"><a:latin typeface="Arial"/><a:ea typeface="Arial"/></a:rPr><a:t>发扬遵义会议精神自觉做到</a:t></a:r>
            <a:r><a:rPr sz="5400" spc="-1380"><a:latin typeface="Arial"/><a:ea typeface="Arial"/></a:rPr><a:t xml:space="preserve"> </a:t></a:r>
            <a:r><a:rPr sz="5400" b="1" spc="50"><a:latin typeface="Arial"/><a:ea typeface="Arial"/></a:rPr><a:t>“两个维护”</a:t></a:r>
          </a:p>
        </p:txBody>
      </p:sp>`;
    const findContainer = (element: HTMLElement) =>
      Array.from(element.querySelectorAll('div')).find(
        (candidate) => candidate.textContent === text && candidate.style.flexDirection === 'column',
      ) as HTMLElement;

    const referenceNode = parseShapeNode(parseXml(shapeXml('none', true)));
    const reference = renderShape(referenceNode, createMockRenderContext());
    document.body.append(reference);
    await document.fonts.ready;
    const referenceContainer = findContainer(reference);
    const range = document.createRange();
    const referenceSpans = referenceContainer.querySelectorAll('span');
    range.setStart(referenceSpans[0].firstChild!, 0);
    range.setEnd(
      referenceSpans[referenceSpans.length - 1].firstChild!,
      referenceSpans[referenceSpans.length - 1].textContent!.length,
    );
    const naturalRect = range.getBoundingClientRect();
    reference.remove();

    const targetNode = parseShapeNode(parseXml(shapeXml('square', false)));
    targetNode.size.w = naturalRect.width - 24;
    targetNode.size.h = naturalRect.height + 4;
    const target = renderShape(targetNode, createMockRenderContext());
    document.body.append(target);
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    const targetContainer = findContainer(target);
    const targetRange = document.createRange();
    const targetSpans = targetContainer.querySelectorAll('span');
    targetRange.setStart(targetSpans[0].firstChild!, 0);
    targetRange.setEnd(
      targetSpans[targetSpans.length - 1].firstChild!,
      targetSpans[targetSpans.length - 1].textContent!.length,
    );
    const lineCount = new Set(
      Array.from(targetRange.getClientRects(), (rect) => Math.round(rect.top)),
    ).size;
    const scale = Number(targetContainer.style.transform.match(/scale\(([^)]+)\)/)?.[1]);

    return {
      lineCount,
      naturalWidth: naturalRect.width,
      targetWidth: targetNode.size.w,
      scale,
    };
  });

  expect(result.naturalWidth - result.targetWidth).toBeCloseTo(24, 1);
  expect(result.lineCount).toBe(1);
  expect(result.scale).toBeGreaterThan(0.98);
  expect(result.scale).toBeLessThan(1);
});

test('headless renderSlide registers and releases host-provided font faces', async ({ page }) => {
  await page.goto('/test/browser/blank.html');
  const result = await page.evaluate(async () => {
    const { parseXml } = await import('/src/parser/XmlParser.ts');
    const { parseShapeNode } = await import('/src/model/nodes/ShapeNode.ts');
    const { renderSlide } = await import('/src/renderer/SlideRenderer.ts');
    const { createMockRenderContext } = await import('/test/unit/helpers/mockContext.ts');
    const ctx = createMockRenderContext();
    const node = parseShapeNode(
      parseXml(`
        <p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <p:nvSpPr><p:cNvPr id="2" name="Configured font"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
          <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1524000" cy="762000"/></a:xfrm><a:prstGeom prst="rect"/></p:spPr>
          <p:txBody>
            <a:bodyPr wrap="none"><a:noAutofit/></a:bodyPr><a:lstStyle/>
            <a:p><a:r><a:rPr sz="2400"><a:latin typeface="Configured Deck Face"/></a:rPr><a:t>Configured font</a:t></a:r></a:p>
          </p:txBody>
        </p:sp>`),
    );
    ctx.slide.nodes = [node];
    const config = [
      {
        family: 'Configured Deck Face',
        source: 'url("/test/browser/missing-font.woff2") format("woff2")',
        descriptors: { weight: '400' },
      },
    ];
    const handle = renderSlide(ctx.presentation, ctx.slide, { fontFaces: config });
    document.body.append(handle.element);
    const registeredFamilies = Array.from(document.fonts, (face) => face.family);
    const registeredImmediately = registeredFamilies.some((family) =>
      family.includes('Configured Deck Face'),
    );
    const family = getComputedStyle(handle.element.querySelector('span')!).fontFamily;
    await handle.ready;
    const registeredAfterReady = Array.from(document.fonts).some((face) =>
      face.family.includes('Configured Deck Face'),
    );
    handle.dispose();
    const registeredAfterDispose = Array.from(document.fonts).some((face) =>
      face.family.includes('Configured Deck Face'),
    );
    return {
      family,
      registeredFamilies,
      registeredImmediately,
      registeredAfterReady,
      registeredAfterDispose,
    };
  });

  expect(result).toEqual(expect.objectContaining({ registeredImmediately: true }));
  expect(result.family).toContain('Configured Deck Face');
  // The missing URL must remove the rejected face while keeping the slide usable.
  expect(result.registeredAfterReady).toBe(false);
  expect(result.registeredAfterDispose).toBe(false);
});
