# Capability Selector Schema

Each selector in `capabilities.json` identifies an OOXML element by package-part glob, namespace,
local name, and optional attribute constraints. It may also use either `parent` or `ancestorPath`
to constrain the element's XML context. The optional `parent` object narrows a match to an element
whose **direct XML parent** has the declared namespace and one of the declared local names:

```json
{
  "partGlob": "ppt/slides/slide*.xml",
  "namespace": "http://schemas.openxmlformats.org/drawingml/2006/main",
  "localName": "scene3d",
  "parent": {
    "namespace": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "localNames": ["grpSpPr", "spPr"]
  }
}
```

`parent` is an exact semantic constraint, not an ancestor search. Its namespace and nonempty,
duplicate-free `localNames` list are both required. Omitting `parent` preserves the original
unscoped selector behavior.

`ancestorPath` matches an exact root-to-direct-parent suffix. Each nonempty step declares a
namespace and one or more accepted local names. This example matches `a:outerShdw` only when it is
the direct effect-list child of a normal PresentationML shape, including that shape inside a group:

```json
{
  "partGlob": "ppt/slides/slide*.xml",
  "namespace": "http://schemas.openxmlformats.org/drawingml/2006/main",
  "localName": "outerShdw",
  "ancestorPath": [
    {
      "namespace": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "localNames": ["sp"]
    },
    {
      "namespace": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "localNames": ["spPr"]
    },
    {
      "namespace": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "localNames": ["effectLst"]
    }
  ]
}
```

The path must be nonempty. Every step requires its namespace and a nonempty, duplicate-free
`localNames` list. A selector cannot combine `parent` and `ancestorPath`. The suffix rule permits
additional package structure above the selected shape while still excluding picture, group-level,
text-run, and theme effect lists. The bounded `a:reflection` capability uses the same three-step
`p:sp` -> `p:spPr` -> `a:effectLst` suffix, so a text, picture, or group reflection cannot enter the
ordinary-shape evidence cohort. Omitting both constraints preserves unscoped matching.

This distinction matters for DrawingML 3D. An `a:scene3d` directly under `p:spPr` or `p:grpSpPr`
is shape/group scene data. The same element directly under `a:bodyPr` is text-body 3D and is
tracked separately. Inventory observation does not imply renderer support; the capability's
`renderMode`, `planningMode`, bounded scope, gates, and a passing current-revision report determine
the support claim. `planningMode` defaults to `ranked`. Set it to `observation-only` for an umbrella
selector that must remain measurable but is too broad to become an executable work packet.
`verificationCases` may list concrete oracle case IDs for pre-merge checks of `fallback` or
`approximate` behavior. These cases keep validation testcase-driven without promoting the
capability to `native` or creating an acceptance record.

The inventory scanner enforces both constraint forms while streaming XML with a bounded ancestor
stack. Contract and scanner behavior are covered by `test_capability_contract.py` and
`test_capability_inventory.py`.

Selectors identify candidate packages; they do not replace the capability's full accepted scope.
Some renderer decisions depend on siblings or descendants that this streaming element selector does
not express. For example, the bounded camera-plane capability selects zero-depth `a:sp3d` and direct
`a:scene3d` candidates under `p:spPr`. The scene selector is needed because PowerPoint can encode an
implicit zero-depth plane without emitting `a:sp3d`. The registry scope additionally constrains the
sibling geometry, scene camera, light, fill, text body, shape style, stroke, transform, bevel,
contour, material, and effects:

```json
{
  "localName": "sp3d",
  "parent": {
    "namespace": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "localNames": ["spPr"]
  },
  "attributes": {
    "contourW": ["$absent", "0"],
    "extrusionH": ["$absent", "0"],
    "prstMaterial": ["$absent"]
  }
}
```

The broader `scene3d` selector is candidate discovery only. It does not promote every scene-only
shape: the native capability currently admits only the exact solid-plane and live editable-text
rows declared in `scope.modalityRows`; all other matches remain residual fallback observations.

The bottom-front capability selects `a:bevelB` as a candidate marker. That element alone does not
prove support: the registry scope and runtime planner additionally require its direct standalone
slide rectangle, exact scene/light/depth/material tuple, opaque paint, text-body contract, and
verified aspect ratio. Group, placeholder, layout, and master provenance is carried in
`RenderContext` and remains a flat fallback even when the descendant `a:bevelB` matches the broad
inventory selector.

The broad `drawingml.shape.3d.scene` row deliberately overlaps bounded native rows and remains a
fallback residual with `planningMode: observation-only`. As a result, inventory still exposes
unverified camera, light, or backdrop values even when the same package also contains a verified
top-bevel or camera-plane candidate, while ranking cannot select the overlapping umbrella directly.
Only a newly registered bounded capability can produce a work packet. A passing current-revision
test report for its exact registry scope supports a public native claim.

The `drawingml.text.3d.scene` row follows the same rule. Its `a:bodyPr/a:scene3d` selector is a
discovery umbrella, and the current representative occurrence belongs to an empty text body, so it
does not prove visible text-3D demand. It remains `observation-only` until a separate bounded row
defines visible text content plus an exact camera, light, text-surface, and fallback contract.

Animation timing is also an umbrella marker. `presentation.animation.timing` selects `p:timing`
only for residual discovery and is `observation-only`. The ranked
`presentation.animation.entrance.fade` selector narrows candidate `p:cTn` elements to the entrance
fade preset tuple under `p:par`. The streaming selector cannot prove the descendant
`p:animEffect`, target kind or paragraph range, duration, visibility set, or trigger relationship;
the declared scope and native temporal matrix must reject candidates outside those bounds.

See `CORPUS_CLASSIFICATION.md` for the separate representative-versus-validation ranking signal.
